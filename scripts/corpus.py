#!/usr/bin/env python3
"""Portable Arknights corpus access. Python 3.10+, standard library only.

Compact evidence or JSON on stdout; progress on stderr. Never calls a language-model API.
"""
from __future__ import annotations

import argparse
import collections
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SCHEMA = 5
DEFAULT_REPO = "3aKHP/arknights-data-pipeline"
SUPPLEMENT_REPO = "3aKHP/ArknightsStoryJson"
ROOT_FILES = {"storyinfo.json", "summaries.json", "event_summaries.json",
              "chardict.json", "charinfo.json", "extrastory.json", "wordcount.json"}
TABLE_SCOPES = {
    "story_review_table.json": "catalog", "story_review_meta_table.json": "metadata",
    "handbook_info_table.json": "dossier", "character_table.json": "operator",
    "char_patch_table.json": "metadata", "char_meta_table.json": "metadata",
    "charword_table.json": "voice", "uniequip_table.json": "module",
    "skin_table.json": "skin", "roguelike_topic_table.json": "rogue",
    "roguelike_table.json": "rogue", "stage_table.json": "stage",
    "campaign_table.json": "stage", "enemy_handbook_table.json": "enemy",
    "item_table.json": "item", "activity_table.json": "activity",
    "sandbox_table.json": "sandbox", "sandbox_perm_table.json": "sandbox",
    "handbook_team_table.json": "metadata", "handbook_table.json": "metadata",
    "chapter_table.json": "metadata", "zone_table.json": "metadata",
    "retro_table.json": "metadata", "medal_table.json": "item",
    "building_data.json": "base",
}
OPTIONAL_TABLES = set(TABLE_SCOPES) - {"story_review_table.json"}
TEXT_KEYS = {"text", "content", "description", "desc", "storytext", "voicetext",
             "uniequipdesc", "dialog", "usage", "itemdesc", "itemusage",
             "obtainapproach", "storyinfo", "teamdes", "teamflavordesc"}
LABEL_KEYS = {"name", "title", "storytitle", "voicetitle", "skinname", "powername",
              "uniequipname", "teamname", "zonename", "stagename"}
NARRATIVE_KEYS = {"storytext", "voicetext", "uniequipdesc", "dialog", "teamflavordesc"}
SKIP_KEYS = {"icon", "iconid", "prefab", "asset", "image", "background", "voiceasset"}
CJK = re.compile(r"[\u3400-\u9fff\U00020000-\U0002ffff]")
ATTR = re.compile(r'''([\w]+)\s*=\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^,\s)]+)''')


class CorpusError(Exception):
    pass


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def progress(message):
    print(message, file=sys.stderr, flush=True)


def load_json(path):
    with Path(path).open(encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def clean(value):
    # Raw strings stay in the index; this is a reversible reading view, not canon.
    value = str(value).replace("{@nickname}", "博士")
    value = re.sub(r"<[^>]*>", "", value)
    return html.unescape(value).strip()


def pointer_part(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def tokens(value):
    """Chinese unigrams + bigrams, latin words. No external tokenizer required."""
    value = clean(value).casefold()
    out = []
    for part in re.findall(r"[\u3400-\u9fff]+|[a-z0-9_]+", value):
        if CJK.search(part):
            out.extend(part)
            out.extend(part[i:i + 2] for i in range(len(part) - 1))
        else:
            out.append(part)
    return " ".join(out)


def default_cache():
    if os.environ.get("ARKNIGHTS_NAVIGATOR_CACHE"):
        return Path(os.environ["ARKNIGHTS_NAVIGATOR_CACHE"]).expanduser()
    if os.environ.get("ARKNIGHTS_STORY_CACHE"):
        return Path(os.environ["ARKNIGHTS_STORY_CACHE"]).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "arknights-story-navigator"
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches/arknights-story-navigator"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "arknights-story-navigator"


def allowed(relative):
    p = PurePosixPath(str(relative).replace("\\", "/"))
    if len(p.parts) == 1:
        return p.name in ROOT_FILES
    if p.parts[:2] == ("gamedata", "excel") and len(p.parts) == 3:
        return p.name in TABLE_SCOPES or p.name == "data_version.txt"
    if p.parts[:2] == ("gamedata", "story"):
        return p.suffix.lower() in {".json", ".txt"} and "info" not in p.parts[2:3]
    return False


def normalize_root(path):
    path = Path(path).expanduser().resolve()
    for candidate in (path / "zh_CN", path):
        if (candidate / "gamedata/excel/story_review_table.json").is_file():
            return candidate
    raise CorpusError("未找到 zh_CN/gamedata/excel/story_review_table.json；请指定语料根目录。")


def get_root(args, required=True):
    supplied = args.root or os.environ.get("ARKNIGHTS_DATA")
    if supplied:
        return normalize_root(supplied)
    active = args.cache / "active.json"
    if active.exists():
        return normalize_root(load_json(active)["root"])
    if required:
        raise CorpusError("没有本地语料。先运行 prepare；或以 --root 指定已有语料。")
    return None


def inventory(root):
    result = []
    for rel in sorted(ROOT_FILES):
        if (root / rel).is_file():
            result.append(root / rel)
    excel = root / "gamedata/excel"
    for name in sorted(TABLE_SCOPES):
        if (excel / name).is_file():
            result.append(excel / name)
    story = root / "gamedata/story"
    if story.exists():
        result.extend(p for p in sorted(story.rglob("*")) if p.is_file() and allowed(p.relative_to(root)))
    return result


def rogue_external_coverage(root, files):
    """Audit known external text IDs; never infer completeness from a directory."""
    table = root / "gamedata/excel/roguelike_topic_table.json"
    refs = set()
    pattern = re.compile(r"(?:month_(?:chat|record)|endbook|challenge)_rogue_\d+(?:_\d+)+")

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if pattern.fullmatch(str(key)):
                    refs.add(str(key))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and pattern.fullmatch(value):
            refs.add(value)

    if table.exists():
        visit(load_json(table))
    stems = {p.stem.casefold() for p in files if "gamedata/story/" in p.as_posix()
             and pattern.fullmatch(p.stem.casefold())}
    missing = sorted(r for r in refs if not any(s == r or s.startswith(r + "_") for s in stems))
    return {"referenced_ids": len(refs), "available_text_files": len(stems), "unresolved_ids": missing,
            "note": "仅核对已知格式的外置月度/结局后记/挑战记录 ID；组 ID 有文件不保证全部段落齐全。"}


def stamp(root):
    rows = [[p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns] for p in inventory(root)]
    for name in (".source.json", ".supplement.json"):
        path = root.parent / name
        if path.exists():
            rows.append(["../" + name, path.stat().st_size, path.stat().st_mtime_ns])
    return rows


def source_info(root):
    meta = root.parent / ".source.json"
    data = load_json(meta) if meta.exists() else {"mode": "local", "version": "unverified-local"}
    version_path = root / "gamedata/excel/data_version.txt"
    if version_path.exists():
        data["game_data_version"] = version_path.read_text(encoding="utf-8-sig").strip()
    return data


def db_path(root, cache):
    key = hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode()).hexdigest()[:20]
    return cache / "indexes" / (key + ".sqlite3")


def request_json(url):
    headers = {"User-Agent": "arknights-story-skill/1.0", "Accept": "application/vnd.github+json"}
    if urllib.parse.urlsplit(url).hostname == "api.github.com" and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=45) as response:
            payload = response.read(8 * 1024 * 1024 + 1)
            if len(payload) > 8 * 1024 * 1024:
                raise CorpusError("远端清单超过大小限制。")
            return json.loads(payload)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CorpusError(f"无法读取公开资料清单（{type(exc).__name__}）。保留已有缓存；可稍后重试或使用本地语料。") from exc


def release_key(release):
    tag = release.get("tag_name", "")
    match = re.fullmatch(r"data(?:rev)?-(\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}_[A-Za-z0-9]+)(?:-r(\d+))?", tag)
    if not match:
        return None
    return (match.group(1), int(match.group(2) or 1))


def choose_release(releases):
    candidates = []
    for r in releases:
        names = {a["name"] for a in r.get("assets", [])}
        key = release_key(r)
        if key and not r.get("draft") and not r.get("prerelease") and {"zh_CN.zip", "manifest.json"} <= names:
            candidates.append((key, r))
    if not candidates:
        raise CorpusError("发布列表中没有可校验的正式剧情包；不会把图片发布或源码 ZIP 当成剧情包。")
    return max(candidates, key=lambda pair: pair[0])[1]


def resolve_release(repo, tag=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise CorpusError("--repo 必须是 owner/repository。")
    endpoint = f"https://api.github.com/repos/{repo}/releases"
    if tag:
        r = request_json(endpoint + "/tags/" + urllib.parse.quote(tag, safe=""))
        return choose_release([r])
    releases = []
    # Traverse public release pages: /latest can point to image assets or miss revisions.
    for page in range(1, 11):
        batch = request_json(endpoint + f"?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise CorpusError("发布列表响应格式不兼容。")
        releases.extend(batch)
        if len(batch) < 100:
            return choose_release(releases)
    raise CorpusError("发布数量超过自动检查上限；请使用 --release 固定版本，避免把不完整扫描当成最新。")


def download(url, destination, expected_size, expected_sha):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise CorpusError("自动下载仅接受 HTTPS。")
    if expected_size <= 0 or expected_size > 512 * 1024 * 1024:
        raise CorpusError("剧情包大小不在支持范围。")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha):
        raise CorpusError("缺少有效 SHA-256。")
    h, count = hashlib.sha256(), 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "arknights-story-skill/1.0"})
        with urllib.request.urlopen(req, timeout=60) as response, Path(destination).open("wb") as target:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > expected_size:
                    raise CorpusError("下载内容超过清单声明大小。")
                h.update(block)
                target.write(block)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CorpusError("下载失败；已有语料和活动版本保持不变。") from exc
    if count != expected_size or h.hexdigest() != expected_sha.lower():
        raise CorpusError("剧情包大小或 SHA-256 不匹配；未激活。")


def fetch_git_blob(url, expected_size, expected_sha):
    if not 0 < expected_size <= 2 * 1024 * 1024 or not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise CorpusError("补充文本的大小或 Git 对象校验值无效。")
    req = urllib.request.Request(url, headers={"User-Agent": "arknights-story-skill/1.0"})
    with urllib.request.urlopen(req, timeout=45) as response:
        raw = response.read(expected_size + 1)
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if len(raw) != expected_size or actual != expected_sha:
        raise CorpusError("补充文本与固定 Git 提交不一致。")
    raw.decode("utf-8-sig")
    return raw


def supplement_rogue(root, fixed_commit=None):
    """Fill missing raw rogue TXT files only, with independent provenance.

    Called only on an unpublished cache snapshot, never a user-provided corpus.
    Failure is visible but does not invalidate the main release.
    """
    summary = {"repo": SUPPLEMENT_REPO, "status": "unavailable", "downloaded_files": 0,
               "note": "独立历史补充来源，不表示与主发布包同版本；仅填缺项，不覆盖正文。"}
    sidecar = root.parent / ".supplement.json"
    previous = load_json(sidecar) if sidecar.exists() else {}
    records, errors = dict(previous.get("files", {})), []
    api = f"https://api.github.com/repos/{SUPPLEMENT_REPO}"
    prefix = "zh_CN/gamedata/story/obt/rogue/"
    try:
        commit = fixed_commit or request_json(api + "/branches/main")["commit"]["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise CorpusError("补充源没有有效固定提交。")
        summary["commit"] = commit
        summary["source_url"] = f"https://github.com/{SUPPLEMENT_REPO}/tree/{commit}"
        parent = request_json(api + "/contents/zh_CN/gamedata/story/obt?ref=" + commit)
        rogue = next((item for item in parent if item.get("name") == "rogue" and item.get("type") == "dir"), None)
        if not rogue:
            raise CorpusError("补充源未提供已知肉鸽目录。")
        tree = request_json(api + "/git/trees/" + rogue["sha"] + "?recursive=1")
        if tree.get("truncated"):
            raise CorpusError("补充来源目录被截断。")
        existing = {p.relative_to(root).with_suffix("").as_posix().casefold() for p in inventory(root)}
        candidates = []
        for item in tree.get("tree", []):
            p = PurePosixPath(item.get("path", ""))
            if p.is_absolute() or ".." in p.parts or any(c in str(p) for c in (":", "\\", "\0")):
                raise CorpusError("补充来源出现不安全路径。")
            if item.get("type") != "blob" or p.suffix != ".txt":
                continue
            rel = "gamedata/story/obt/rogue/" + p.as_posix()
            if str(PurePosixPath(rel).with_suffix("")).casefold() not in existing:
                candidates.append((rel, item))
        if len(candidates) > 1000 or sum(i.get("size", 0) for _, i in candidates) > 20 * 1024**2:
            raise CorpusError("补充来源超过预期规模，需检查格式。")

        def fetch(candidate):
            rel, item = candidate
            source_path = prefix + item["path"]
            url = f"https://raw.githubusercontent.com/{SUPPLEMENT_REPO}/{commit}/" + urllib.parse.quote(source_path, safe="/")
            raw = fetch_git_blob(url, item.get("size", 0), item.get("sha", ""))
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            return rel, {"repo": SUPPLEMENT_REPO, "commit": commit, "raw_url": url,
                         "source_url": f"https://github.com/{SUPPLEMENT_REPO}/blob/{commit}/" + source_path,
                         "git_blob_sha1": item["sha"], "sha256": hashlib.sha256(raw).hexdigest(),
                         "version_relation": "independent_historical_supplement"}

        progress(f"补充缺失的肉鸽外置文本：{len(candidates)} 个候选文件……")
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(fetch, row): row[0] for row in candidates}
            for future in as_completed(futures):
                try:
                    rel, info = future.result()
                    records[rel] = info
                except (OSError, UnicodeError, CorpusError) as exc:
                    errors.append({"file": futures[future], "error": type(exc).__name__})
                if (len(records) + len(errors)) % 50 == 0:
                    progress(f"已检查 {len(records) + len(errors)}/{len(candidates)} 个补充文件……")
        summary.update(status="partial" if errors else "complete_for_discovered_candidates",
                       candidate_files=len(candidates), downloaded_files=len(records))
    except (CorpusError, ValueError, TypeError, KeyError) as exc:
        errors.append({"error": type(exc).__name__, "detail": str(exc)[:180]})
    summary["errors"] = errors
    save_json(root.parent / ".supplement.json", {"summary": summary, "files": records})
    return summary


def extract_archive(archive, destination):
    total, count = 0, 0
    seen = set()
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            raw = item.filename.replace("\\", "/")
            p = PurePosixPath(raw)
            if p.is_absolute() or ".." in p.parts or ":" in raw or "\x00" in raw:
                raise CorpusError("压缩包包含不安全路径。")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise CorpusError("压缩包不应包含符号链接。")
            if item.is_dir() or not p.parts or p.parts[0] != "zh_CN":
                continue
            rel = PurePosixPath(*p.parts[1:])
            if not allowed(rel):
                continue
            folded = str(p).casefold()
            if folded in seen:
                raise CorpusError("压缩包存在大小写重复路径，跨平台含义不确定。")
            seen.add(folded)
            total += item.file_size
            count += 1
            if item.file_size > 150 * 1024 * 1024 or total > 2 * 1024**3 or count > 50000:
                raise CorpusError("压缩包超过解压限制。")
            target = destination.joinpath(*p.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(item) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    root = normalize_root(destination)
    # Parse the essential directory and verify each referenced story exists.
    review = load_json(root / "gamedata/excel/story_review_table.json")
    available = {p.relative_to(root / "gamedata/story").with_suffix("").as_posix().casefold()
                 for p in inventory(root) if "gamedata/story/" in p.relative_to(root).as_posix()}
    missing = [s.get("storyTxt") for e in review.values() for s in e.get("infoUnlockDatas", [])
               if s.get("storyTxt") and s["storyTxt"].casefold() not in available]
    if missing:
        raise CorpusError(f"剧情包缺少 {len(missing)} 个目录引用，例：{missing[:3]}。未激活。")
    return root


def prepare(args):
    if args.root or os.environ.get("ARKNIGHTS_DATA"):
        root = get_root(args)
        return build_index(root, args.cache)
    existing = get_root(args, required=False)
    if existing and not args.update and not args.archive and not args.release:
        return build_index(existing, args.cache)
    args.cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="download-", dir=args.cache) as temporary:
        work = Path(temporary)
        if args.archive:
            archive = Path(args.archive).expanduser().resolve()
            if not args.sha256 or digest_file(archive) != args.sha256.lower():
                raise CorpusError("导入本地 ZIP 时必须提供匹配的 --sha256。")
            identity = "import-" + args.sha256[:16]
            info = {"mode": "archive", "version": identity, "sha256": args.sha256,
                    "verified": "user-supplied-sha256"}
        else:
            progress("检查正式剧情数据发布与修订……")
            release = resolve_release(args.repo, args.release)
            assets = {a["name"]: a for a in release["assets"]}
            manifest = request_json(assets["manifest.json"]["browser_download_url"])
            if manifest.get("contractVersion") != "prts-mcp-data/v1":
                raise CorpusError("远端数据契约发生变化，需检查适配器后再获取。")
            entry = manifest.get("assets", {}).get("zh_CN.zip", {})
            version = manifest.get("source", {}).get("versionId")
            revision = int(manifest.get("publicationRevision", 1))
            if release_key(release) != (version, revision):
                raise CorpusError("发布标签与清单的源版本/修订号不一致。")
            expected_sha, size = entry.get("sha256", ""), entry.get("size", 0)
            if size != assets["zh_CN.zip"].get("size"):
                raise CorpusError("GitHub 资产大小与清单不一致。")
            identity = release["tag_name"] + "-" + expected_sha[:12]
            info = {"mode": "release", "repo": args.repo, "version": version, "revision": revision,
                    "release": release["tag_name"], "source_url": release["html_url"],
                    "archive_url": assets["zh_CN.zip"]["browser_download_url"],
                    "sha256": expected_sha, "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "verified": "manifest-sha256", "contract": manifest["contractVersion"]}
            archive = work / "zh_CN.zip"
            progress(f"下载并校验剧情包（{size / 1024**2:.1f} MiB）……")
            download(info["archive_url"], archive, size, expected_sha)
            save_json(work / "manifest.json", manifest)
        final = args.cache / "snapshots" / identity
        if final.exists():
            # Never overwrite an already activated snapshot in place.
            final = final.with_name(final.name + "-" + str(time.time_ns()))
        staged = work / "snapshot"
        staged.mkdir()
        staged_root = extract_archive(archive, staged)
        if not args.archive and not args.no_supplement and args.repo == DEFAULT_REPO:
            info["supplement"] = supplement_rogue(staged_root, args.supplement_ref)
        save_json(staged / ".source.json", info)
        if (work / "manifest.json").exists():
            shutil.copy2(work / "manifest.json", staged / "manifest.json")
        final.parent.mkdir(parents=True, exist_ok=True)
        staged.rename(final)
        root = normalize_root(final)
        report = build_index(root, args.cache)
        save_json(args.cache / "active.json", {"root": str(root), "source": info})
        return report


def block(locator, text, speaker="", kind="narration", **extras):
    return {"locator": locator, "text": clean(text), "raw_text": str(text),
            "speaker": clean(speaker), "kind": kind, **extras}


def option_values(value):
    if isinstance(value, list):
        return [str(v.get("text", "")) if isinstance(v, dict) else str(v) for v in value]
    return str(value or "").split(";")


def story_nodes(data):
    result, active = [], None
    for i, node in enumerate(data.get("storyList") or []):
        prop = str(node.get("prop") or "").lower()
        attrs = {str(k).lower(): v for k, v in (node.get("attributes") or {}).items()}
        loc = f"/storyList/{i}"
        common = {"node_id": node.get("id", i), "prop": prop, "branch": active}
        if prop == "decision":
            options = option_values(attrs.get("options"))
            values = option_values(attrs.get("values"))
            result.append(block(loc, "\n".join(f"[{values[j] if j < len(values) else j + 1}] {t}"
                                                for j, t in enumerate(options)), kind="choice",
                                target=node.get("targetLine"), **common))
        elif prop == "predicate":
            active = None if node.get("endOfOpt") else attrs.get("references")
            result.append(block(loc, "分支条件：" + str(attrs.get("references", "")), kind="branch",
                                target=node.get("targetLine"), end_of_option=node.get("endOfOpt"), **{**common, "branch": active}))
        else:
            content = attrs.get("content") or attrs.get("text")
            # joined repeats earlier multiline fragments: use it only when content is absent.
            if prop == "multiline" and not content:
                content = attrs.get("joined")
            if content:
                kind = "dialogue" if prop in {"name", "multiline"} and attrs.get("name") else "narration"
                if prop not in {"name", "multiline", "sticker", "subtitle", "animtext", "dialog"}:
                    kind = "other_text"
                result.append(block(loc, content, attrs.get("name", ""), kind,
                                    figure=node.get("figure_art"), **common))
    return result


def raw_story(text, names):
    result, branch = [], None
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        tag = re.match(r"^\[([^\]]+)\](.*)$", stripped)
        if not tag:
            result.append(block(f"line:{line_no}", stripped, branch=branch))
            continue
        head, content = tag.groups()
        prop_match = re.match(r"\w+", head)
        prop = prop_match.group().lower() if prop_match else ""
        attrs = {k.lower(): v.strip("\"'") for k, v in ATTR.findall(head)}
        if prop == "predicate":
            branch = attrs.get("references")
            result.append(block(f"line:{line_no}", "分支条件：" + str(branch), kind="branch", branch=branch))
        elif prop == "decision":
            result.append(block(f"line:{line_no}", "\n".join(option_values(attrs.get("options"))),
                                kind="choice", values=attrs.get("values"), branch=branch))
        elif prop in {"title", "div"}:
            result.append(block(f"line:{line_no}", content, kind="section", branch=branch))
        elif prop in {"name", "dialog", "multiline", "subtitle", "sticker", "animtext"} or content:
            raw = content or attrs.get("content") or attrs.get("text", "")
            speaker_id = attrs.get("head", "")
            speaker = attrs.get("name") or names.get(speaker_id) or speaker_id
            if raw:
                result.append(block(f"line:{line_no}", raw, speaker,
                                    "dialogue" if speaker else "narration", branch=branch, speaker_id=speaker_id))
    return result


def table_records(value, pointer="", context=None):
    """Yield source dictionaries containing display text, preserving exact JSON pointers.

    Broad CJK fallback captures new activity/rogue text keys; it is explicitly
    record text, not a claim that every field is narrative or canonical history.
    """
    context = dict(context or {})
    if isinstance(value, dict):
        for key in ("charId", "charID", "storyTitle", "voiceTitle", "topicId", "id"):
            if isinstance(value.get(key), (str, int)):
                context[key] = value[key]
        labels, strings = [], []
        for key, v in value.items():
            if not isinstance(v, str) or not v.strip() or key.lower() in SKIP_KEYS:
                continue
            lower = key.lower()
            if lower in LABEL_KEYS and (CJK.search(v) or not re.fullmatch(r"[a-z0-9_/#.-]+", v)):
                labels.append(clean(v))
            if lower in TEXT_KEYS or CJK.search(v):
                strings.append(block(pointer + "/" + pointer_part(key), v, kind="table_text", field=key))
        if labels:
            context["label"] = " · ".join(dict.fromkeys(labels))
        if strings:
            # Keep explicit source fields only. Lists must be flat scalar IDs;
            # nested objects are not relationships, and prose is never mined for IDs.
            refs = {k: v for k, v in value.items()
                    if (k.lower().endswith("id") or k.lower().endswith("ids"))
                    and (isinstance(v, (str, int, float, bool))
                         or isinstance(v, list) and all(type(item) in (str, int) for item in v))}
            yield pointer, strings, {**context, "links": refs}
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                yield from table_records(child, pointer + "/" + pointer_part(key), context)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            p = pointer + "/" + str(i)
            if isinstance(child, (dict, list)):
                yield from table_records(child, p, context)
            elif isinstance(child, str) and (CJK.search(child) or pointer.split("/")[-1].lower() in TEXT_KEYS):
                yield p, [block(p, child, kind="table_text")], context


def story_catalog(root):
    review = load_json(root / "gamedata/excel/story_review_table.json")
    entries, by_path = [], {}
    for event_id, event in review.items():
        rows = []
        for item in sorted(event.get("infoUnlockDatas") or [], key=lambda s: s.get("storySort", 0)):
            key = item.get("storyTxt")
            if not key:
                continue
            meta = {"event_id": event_id, "event_name": event.get("name", ""),
                    "entry_type": event.get("entryType"), "story_key": key,
                    "title": " ".join(str(item.get(k) or "") for k in ("storyCode", "storyName", "avgTag")).strip(),
                    "sort": item.get("storySort"), "published_timestamp": event.get("startTime"),
                    "dependency": item.get("storyDependence")}
            by_path[key.casefold()] = meta
            rows.append(meta)
        entries.append({"event_id": event_id, "name": event.get("name"), "entry_type": event.get("entryType"),
                        "published_timestamp": event.get("startTime"), "stories": rows})
    return entries, by_path


def character_names(root):
    path = root / "gamedata/excel/character_table.json"
    names, entities = {}, []
    if path.exists():
        for cid, char in load_json(path).items():
            name = char.get("name", "")
            if name:
                names[cid] = name
                entities.append({"id": cid, "name": name, "appellation": char.get("appellation"),
                                 "nation_id": char.get("nationId"), "group_id": char.get("groupId"),
                                 "team_id": char.get("teamId")})
    return names, entities


def source_group(relative):
    """Compact retrieval facet, not a claim of independent provenance."""
    path = PurePosixPath(relative)
    return str(path.parent) if path.parts[:2] == ("gamedata", "story") else relative


def narrative_utility(blocks, scope):
    """A small structural prior; no lore, inferred canon, or keyword classifier."""
    if scope in {"summary", "synopsis", "metadata"}:
        return 0
    if any(b["kind"] in {"dialogue", "narration", "other_text"}
           or b.get("field", "").lower() in NARRATIVE_KEYS for b in blocks):
        return 2
    if any(b["kind"] == "choice" or b["kind"] == "table_text"
           and b.get("field", "").lower() not in LABEL_KEYS for b in blocks):
        return 1
    return 0


def build_index(root, cache):
    root = normalize_root(root)
    cache = Path(cache).expanduser().resolve()
    path = db_path(root, cache)
    current_stamp = stamp(root)
    if path.exists():
        con = sqlite3.connect(path)
        try:
            meta = dict(con.execute("SELECT key,value FROM metadata"))
            if json.loads(meta.get("stamp", "null")) == current_stamp and int(meta.get("schema", "0")) == SCHEMA:
                return {"ready": True, "reused": True, "root": str(root), "index": str(path),
                        "source": source_info(root), "coverage": json.loads(meta["coverage"])}
        finally:
            con.close()
    stats = collections.Counter()
    unknown_keys = collections.Counter()
    hashes, errors, unsupported = {}, [], []
    names, entities = character_names(root)
    events, catalog = story_catalog(root)
    files = inventory(root)
    supplement_path = root.parent / ".supplement.json"
    supplements = {key.casefold(): value for key, value in load_json(supplement_path).get("files", {}).items()} if supplement_path.exists() else {}
    story_paths = {p.relative_to(root).with_suffix("").as_posix().casefold()
                   for p in files if p.suffix == ".json" and "gamedata/story/" in p.relative_to(root).as_posix()}
    available_stories = {p.relative_to(root / "gamedata/story").with_suffix("").as_posix().casefold()
                         for p in files if p.relative_to(root).as_posix().startswith("gamedata/story/")}
    missing = [key for key in catalog if key not in available_stories]
    has_handbook = (root / "gamedata/excel/handbook_info_table.json").exists()
    handbook_ids = set()
    if has_handbook:
        handbook_ids = set(load_json(root / "gamedata/excel/handbook_info_table.json").get("handbookDict", {}))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="index-", suffix=".sqlite3", dir=path.parent)
    os.close(fd)
    temp = Path(temporary)
    con = sqlite3.connect(temp)
    try:
        con.executescript("""
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE documents(id TEXT PRIMARY KEY,path TEXT,pointer TEXT,scope TEXT,event_id TEXT,
                                   title TEXT,source_sha256 TEXT,meta TEXT,blocks TEXT,source_group TEXT);
            CREATE TABLE chunks(id INTEGER PRIMARY KEY,doc_id TEXT,start INTEGER,end INTEGER,text TEXT,
                                narrative_utility INTEGER);
            CREATE INDEX chunks_doc ON chunks(doc_id);
            CREATE INDEX docs_scope ON documents(scope,event_id);
        """)
        fts = True
        try:
            con.execute("CREATE VIRTUAL TABLE search_fts USING fts5(terms, tokenize='unicode61')")
        except sqlite3.OperationalError:
            fts = False

        def add(relative, pointer, scope, title, blocks, meta):
            blocks = [b for b in blocks if b["text"]]
            if not blocks:
                return
            ident = hashlib.sha256((relative + "#" + pointer).encode()).hexdigest()[:24]
            meta = {**meta, "narrative_context": "roguelike_unclassified" if scope == "rogue" else "unclassified",
                    "evidence_type": "generated_navigation" if scope == "summary" else
                                     "official_synopsis" if scope == "synopsis" else "game_text"}
            if relative.casefold() in supplements:
                meta["file_source"] = supplements[relative.casefold()]
            con.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (ident, relative, pointer, scope, meta.get("event_id", ""), title,
                         hashes[relative], json.dumps(meta, ensure_ascii=False), json.dumps(blocks, ensure_ascii=False),
                         source_group(relative)))
            stats[scope + "_documents"] += 1
            stats["blocks"] += len(blocks)
            start = 0
            while start < len(blocks):
                end, size = start, 0
                while end < len(blocks) and end - start < 24 and (size < 1800 or end == start):
                    size += len(blocks[end]["text"])
                    end += 1
                text = "\n".join((b["speaker"] + "：" if b["speaker"] else "") + b["text"] for b in blocks[start:end])
                cur = con.execute("INSERT INTO chunks(doc_id,start,end,text,narrative_utility) VALUES(?,?,?,?,?)",
                                  (ident, start, end, text, narrative_utility(blocks[start:end], scope)))
                if fts:
                    con.execute("INSERT INTO search_fts(rowid,terms) VALUES(?,?)", (cur.lastrowid, tokens(title + "\n" + text)))
                stats["chunks"] += 1
                if end == len(blocks):
                    break
                start = max(start + 1, end - 2)

        for number, file in enumerate(files):
            rel = file.relative_to(root).as_posix()
            raw = file.read_bytes()
            hashes[rel] = hashlib.sha256(raw).hexdigest()
            try:
                if rel.startswith("gamedata/story/"):
                    if file.name == "story_variables.json":
                        stats["non_narrative_story_files"] += 1
                        continue
                    key = file.relative_to(root / "gamedata/story").with_suffix("").as_posix()
                    meta = dict(catalog.get(key.casefold(), {"story_key": key}))
                    meta["option_trace_verified"] = False
                    scope = "rogue" if "/rogue" in rel.casefold() else "memory" if "/memory/" in rel else "story"
                    if file.suffix.lower() == ".json":
                        data = json.loads(raw)
                        if not isinstance(data, dict) or "storyList" not in data:
                            stats["unsupported_story_files"] += 1
                            unsupported.append(rel)
                            continue
                        meta["option_trace_verified"] = data.get("OPTIONTRACE") is True
                        meta.setdefault("event_id", data.get("eventid", ""))
                        meta.setdefault("event_name", data.get("eventName", ""))
                        title = meta.get("title") or data.get("storyName") or key
                        add(rel, "", scope, title, story_nodes(data), meta)
                        if data.get("storyInfo"):
                            add(rel, "/storyInfo", "synopsis", title, [block("/storyInfo", data["storyInfo"], kind="synopsis")], meta)
                    elif rel[:-4].casefold() not in story_paths:
                        add(rel, "", scope, meta.get("title", key), raw_story(raw.decode("utf-8-sig"), names), meta)
                    else:
                        stats["duplicate_raw_story_files"] += 1
                elif file.name in {"summaries.json", "event_summaries.json", "storyinfo.json"}:
                    for key, text in json.loads(raw).items():
                        if isinstance(text, str) and text.strip():
                            scope = "synopsis" if file.name == "storyinfo.json" else "summary"
                            meta = dict(catalog.get(key.casefold(), {}))
                            if file.name == "event_summaries.json":
                                meta["event_id"] = key
                            add(rel, "/" + pointer_part(key), scope, meta.get("title", key),
                                [block("/" + pointer_part(key), text, kind=scope)], meta)
                elif file.name == "charinfo.json":
                    for cid, value in json.loads(raw).items():
                        if cid in handbook_ids:
                            continue
                        for ptr, blocks, meta in table_records(value, "/" + pointer_part(cid), {"charID": cid}):
                            add(rel, ptr, "dossier", names.get(cid, value.get("name", cid)), blocks, meta)
                elif file.name in TABLE_SCOPES and TABLE_SCOPES[file.name] != "catalog":
                    scope = TABLE_SCOPES[file.name]
                    for ptr, blocks, meta in table_records(json.loads(raw)):
                        cid = meta.get("charId") or meta.get("charID")
                        if not cid:
                            cid = next((part for part in ptr.split("/") if part in names), None)
                        if cid:
                            meta["character_id"] = cid
                            meta["character_name"] = names.get(cid, cid)
                        topic = re.search(r"/(rogue_\d+)(?:/|$)", ptr)
                        if topic:
                            meta["topic_id"] = topic.group(1)
                        title = " · ".join(str(v) for v in (meta.get("character_name"), meta.get("storyTitle"),
                                                           meta.get("voiceTitle"), meta.get("label")) if v) or ptr
                        for b in blocks:
                            if b.get("field", "").lower() not in TEXT_KEYS | LABEL_KEYS:
                                unknown_keys[b.get("field", "")] += 1
                        add(rel, ptr, scope, title, blocks, meta)
                stats["parsed_files"] += 1
            except (ValueError, TypeError, KeyError, UnicodeError) as exc:
                errors.append({"file": rel, "error": type(exc).__name__})
            if number % 500 == 0:
                progress(f"索引 {number + 1}/{len(files)} 个语料文件……")
        if errors:
            raise CorpusError("语料解析失败，未替换旧索引：" + json.dumps(errors[:8], ensure_ascii=False))
        fingerprint = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        coverage = {"counts": dict(stats), "story_catalog_entries": len(catalog), "event_entries": len(events),
                    "missing_catalog_stories": missing,
                    "unsupported_story_files": unsupported,
                    "rogue_external_records": rogue_external_coverage(root, files),
                    "supplementary_files": len(supplements),
                    "missing_optional_tables": sorted(t for t in OPTIONAL_TABLES if not (root / "gamedata/excel" / t).exists()),
                    "fallback_text_fields": dict(unknown_keys.most_common(40)),
                    "excluded": ["evals", "tests", "scripts", ".git", "other_languages", "non_allowlisted_tables", "story/info"],
                    "limits": ["不含未取得的游戏外官方资料", "不自动判定叙事分支的正史地位", "表格文本包含部分界面/玩法说明"],
                    "fts5": fts, "fingerprint": fingerprint}
        for key, value in {"schema": SCHEMA, "stamp": current_stamp, "coverage": coverage,
                           "events": events, "entities": entities, "source": source_info(root),
                           "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}.items():
            con.execute("INSERT INTO metadata VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))
        con.commit()
        con.close()
        os.replace(temp, path)
        return {"ready": True, "root": str(root), "index": str(path), "source": source_info(root), "coverage": coverage}
    finally:
        con.close()
        if temp.exists():
            temp.unlink()


def open_index(args):
    root = get_root(args)
    path = db_path(root, args.cache)
    if not path.exists():
        raise CorpusError("尚无索引。先运行 prepare（已有语料时加 --root）。")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    meta = {r["key"]: json.loads(r["value"]) for r in con.execute("SELECT * FROM metadata")}
    if meta.get("schema") != SCHEMA or meta.get("stamp") != stamp(root):
        con.close()
        raise CorpusError("语料或解析器已改变。请重新运行 index，不能继续使用旧索引。")
    return root, con, meta


def snippet(text, terms, length=420):
    pos = next((text.casefold().find(t.casefold()) for t in terms if t.casefold() in text.casefold()), 0)
    start = max(0, pos - 100)
    return ("…" if start else "") + text[start:start + length] + ("…" if start + length < len(text) else "")


def rank_candidates(rows):
    """Round-robin sources and documents within equal relevance tiers.

    All candidates participate before pagination: no deduplication or per-source
    quota can hide a hit. Repetition counts do not increase query strength.
    """
    def strength(row):
        return (-row["exact_terms"], -row["body_terms"], -row["narrative_utility"])

    def tie(row):
        return (row["lexical_score"], row["id"])

    documents = collections.Counter()
    for row in sorted(rows, key=lambda r: (*strength(r), *tie(r))):
        key = (strength(row), row["doc_id"])
        documents[key] += 1
        row["document_rank"] = documents[key]
    sources = collections.Counter()
    for row in sorted(rows, key=lambda r: (*strength(r), r["document_rank"], *tie(r))):
        key = (strength(row), row["source_group"])
        sources[key] += 1
        row["source_rank"] = sources[key]
    return sorted(rows, key=lambda r: (*strength(r), r["source_rank"], r["document_rank"], *tie(r)))


def search_facets(rows, key):
    groups = {}
    for row in rows:
        group = groups.setdefault(row[key], {"chunks": 0, "documents": set(), "files": set()})
        group["chunks"] += 1
        group["documents"].add(row["doc_id"])
        group["files"].add(row["path"])
    return [{key: name, "total_chunks": group["chunks"], "total_documents": len(group["documents"]),
             "total_source_files": len(group["files"])} for name, group in sorted(groups.items())]


def search(args):
    terms = [clean(t) for t in [args.query, *(args.term or [])] if t and clean(t)]
    if not terms:
        raise CorpusError("请输入一个检索词；长问题请拆成名字、术语或短语，使用 --term 增加检索词。")
    root, con, meta = open_index(args)
    conditions, params = [], []
    if args.scope:
        conditions.append("d.scope IN (" + ",".join("?" for _ in args.scope) + ")")
        params.extend(args.scope)
    elif not args.include_navigation:
        conditions.append("d.scope NOT IN ('summary','synopsis','metadata')")
    if args.event:
        conditions.append("d.event_id=?")
        params.append(args.event)
    if args.path:
        conditions.append("instr(lower(d.path),lower(?))>0")
        params.append(args.path.replace("\\", "/"))
    if args.mode == "lexical":
        if not meta["coverage"]["fts5"]:
            con.close()
            raise CorpusError("当前 Python 的 SQLite 没有 FTS5，请用 --mode any 或 all 做精确检索。")
        pieces = list(dict.fromkeys(tokens(" ".join(terms)).split()))[:100]
        if not pieces:
            con.close()
            raise CorpusError("没有可用于词项检索的字符。")
        conditions.append("search_fts MATCH ?")
        params.append(" OR ".join('"' + t.replace('"', '""') + '"' for t in pieces))
        join = "JOIN search_fts ON search_fts.rowid=c.id"
        lexical_score = "bm25(search_fts)"
    else:
        join, lexical_score = "", "0"
        op = " AND " if args.mode == "all" else " OR "
        conditions.append("(" + op.join("instr(lower(c.text || char(10) || d.title),lower(?))>0" for _ in terms) + ")")
        params.extend(terms)
    sql = " FROM chunks c JOIN documents d ON d.id=c.doc_id " + join + " WHERE " + " AND ".join(conditions)
    # Materialize compact scoring data only, not every chunk's text or blocks.
    # This works without SQLite window functions and keeps exhaustive pagination.
    # Match SQLite's built-in lower(), which folds ASCII rather than all Unicode.
    ascii_lower = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    rank_terms = list(dict.fromkeys(t.translate(ascii_lower) for t in terms))
    exact = " + ".join("(instr(lower(c.text || char(10) || d.title),?)>0)" for _ in rank_terms)
    body = " + ".join("(instr(lower(c.text),?)>0)" for _ in rank_terms)
    candidates = [dict(row) for row in con.execute(
        "SELECT c.id,c.doc_id,c.narrative_utility,d.path,d.scope,d.source_group,d.event_id," +
        exact + " AS exact_terms," + body + " AS body_terms," + lexical_score + " AS lexical_score" + sql,
        rank_terms + rank_terms + params)]
    total, docs = len(candidates), len({r["doc_id"] for r in candidates})
    facets = {"scopes": search_facets(candidates, "scope"),
              "sources": search_facets(candidates, "source_group")}
    order = getattr(args, "order", "relevance")
    ordered = rank_candidates(candidates) if order == "relevance" else sorted(candidates, key=lambda r: r["id"])
    selected = ordered[args.offset:args.offset + args.limit]
    grouped = getattr(args, "group_sources", False)
    if grouped:
        # Group the complete hit set BEFORE pagination; a busy first source must
        # not hide smaller sources. Samples are navigation, not exhaustive facts.
        groups = {}
        event_names = {e["event_id"]: e["name"] for e in meta["events"]}
        for candidate in ordered:
            key = ("event:" + candidate["event_id"] if candidate["event_id"]
                   else "source:" + candidate["source_group"])
            group = groups.setdefault(key, {"group_id": key,
                "event_id": candidate["event_id"],
                "title": event_names.get(candidate["event_id"], candidate["source_group"]),
                "documents": set(), "scopes": set(), "total_chunks": 0,
                "navigation_documents": set(), "samples": []})
            group["documents"].add(candidate["doc_id"])
            group["scopes"].add(candidate["scope"])
            group["total_chunks"] += 1
            if candidate["scope"] in {"synopsis", "summary", "metadata"}:
                group["navigation_documents"].add(candidate["doc_id"])
            if len(group["samples"]) < 2 and all(
                    sample["doc_id"] != candidate["doc_id"] for sample in group["samples"]):
                group["samples"].append(candidate)
        page_groups = list(groups.values())[args.offset:args.offset + args.limit]
        selected = [sample for group in page_groups for sample in group["samples"]]
    ids = [row["id"] for row in selected]
    rows = {row["id"]: row for row in con.execute(
        "SELECT c.*,d.path,d.pointer,d.title,d.scope,d.event_id,d.source_sha256,d.meta,d.source_group"
        " FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE c.id IN (" +
        ",".join("?" for _ in ids) + ")", ids)} if ids else {}
    results = []
    for candidate in selected:
        row = rows[candidate["id"]]
        results.append({"doc_id": row["doc_id"], "title": row["title"], "scope": row["scope"],
                        "event_id": row["event_id"], "source_file": row["path"], "record_pointer": row["pointer"],
                        "source_group": row["source_group"],
                        "block_start": row["start"], "block_end_exclusive": row["end"],
                        "source_sha256": row["source_sha256"], "metadata": json.loads(row["meta"]),
                        "match": {"exact_terms": candidate["exact_terms"], "body_terms": candidate["body_terms"],
                                  "narrative_utility": candidate["narrative_utility"]},
                        "read_hint": {"id": row["doc_id"], "around": row["start"], "context": 12,
                                      "limit": max(60, row["end"] - row["start"] + 12)},
                        "snippet": snippet(row["text"], terms)})
    con.close()
    if grouped:
        hits = {hit["doc_id"]: hit for hit in results}
        items = []
        for group in page_groups:
            items.append({"group_id": group["group_id"], "event_id": group["event_id"],
                "title": group["title"], "scopes": sorted(group["scopes"]),
                "total_chunks": group["total_chunks"], "total_documents": len(group["documents"]),
                "navigation_documents": len(group["navigation_documents"]),
                "text_documents": len(group["documents"] - group["navigation_documents"]),
                "outline_hint": {"event": group["event_id"]} if group["event_id"] in event_names else None,
                "samples": [hits[sample["doc_id"]] for sample in group["samples"]]})
        more = args.offset + len(items) < len(groups)
        return {"kind": "find", "source": meta["source"], "fingerprint": meta["coverage"]["fingerprint"],
            "terms": terms, "mode": args.mode, "total_groups": len(groups), "total_chunks": total,
            "total_documents": docs, "offset": args.offset, "has_more": more,
            "next_offset": args.offset + len(items) if more else None, "groups": items,
            "note": "按来源分组后分页，每组最多两个不同条目样例；摘要命中不是原文证据。"
                    "数量只统计本次查询，不表示全集覆盖或事件次数。可按 event/scope/path 继续 search。"}
    return {"source": meta["source"], "fingerprint": meta["coverage"]["fingerprint"], "mode": args.mode,
            "terms": terms, "order": order, "total_chunks": total, "total_documents": docs,
            "total_source_files": len({r["path"] for r in candidates}), "facets": facets,
            "offset": args.offset, "has_more": args.offset + len(results) < total,
            "next_offset": args.offset + len(results) if args.offset + len(results) < total else None,
            "note": "结果为定位片段；先 read --around block_start 展开上下文。all 要求词在同一窗口内。"
                    "默认排序按精确词覆盖、正文命中、结构性叙事优先级，再交错来源与文档；不判断正史或可信度。"
                    "facets 是当前过滤条件下分页前的全部命中；source_group 按正文目录或表文件分组，不代表独立证据。",
            "results": results}


def read_document(args):
    root, con, meta = open_index(args)
    row = con.execute("SELECT * FROM documents WHERE id=?", (args.id,)).fetchone()
    if not row:
        con.close()
        raise CorpusError("该 doc_id 不属于当前索引。")
    # Strong check for cited evidence even if somebody preserved file stat metadata.
    if digest_file(root / row["path"]) != row["source_sha256"]:
        con.close()
        raise CorpusError("原文件内容已改变，请重建索引后重新读取。")
    all_blocks = json.loads(row["blocks"])
    around = getattr(args, "around", None)
    locator = getattr(args, "locator", None)
    locator_matches = []
    if locator is not None:
        locator_matches = [i for i, block in enumerate(all_blocks) if block.get("locator") == locator]
        if not locator_matches:
            con.close()
            raise CorpusError("本文没有这个原文定位。请复制 read 返回的 locator（JSON Pointer 或 line:N）；不要把 node_id 当块索引。")
        around = locator_matches[0]
    start = args.start
    if around is not None:
        context = getattr(args, "context", 12)
        if not 0 <= around < len(all_blocks) or context < 0:
            con.close()
            raise CorpusError(f"around 必须是块索引 0..{len(all_blocks) - 1}，不是原始 node_id。"
                              "请复制搜索结果 read_hint，或用 --locator 传入原文位置；context 不能为负数。")
        # --limit still caps the entire window; an anchor is never clipped out.
        start = max(0, around - min(context, args.limit - 1))
    end = min(len(all_blocks), start + args.limit)
    blocks = [{"index": i, **b} for i, b in enumerate(all_blocks[start:end], start)]
    siblings = [dict(r) for r in con.execute("SELECT id,title,pointer FROM documents WHERE path=? AND id!=? LIMIT 30",
                                           (row["path"], row["id"]))] if row["pointer"] else []
    con.close()
    return {"source": meta["source"], "doc_id": row["id"], "title": row["title"], "scope": row["scope"],
            "source_file": row["path"], "source_sha256": row["source_sha256"], "record_pointer": row["pointer"],
            "metadata": json.loads(row["meta"]), "total_blocks": len(all_blocks), "blocks": blocks,
            "block_start": start, "block_end_exclusive": end, "around": around, "has_previous": start > 0,
            "has_more": end < len(all_blocks), "next_start": end if end < len(all_blocks) else None,
            "requested_locator": locator, "locator_matches": locator_matches,
            "sibling_records_sample": siblings}


def compact_response(data):
    """Readable evidence without repeated transport metadata. JSON remains lossless.

    This formatter neither summarizes prose nor ranks or filters results. Source
    links, exact text, branch markers, record links and continuation stay visible.
    """
    if data.get("kind") in {"find", "outline"}:
        return navigation_response(data)

    def j(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def source_lines(source, label="source"):
        fields = {k: source[k] for k in ("mode", "repo", "release", "version", "revision", "commit", "source_url")
                  if k in source}
        return [label + " " + j(fields)] if fields else []

    def context_lines(record):
        lines = []
        metadata = record.get("metadata") or {}
        if record.get("record_pointer"):
            lines.append("record " + record["record_pointer"])
        if metadata.get("file_source"):
            lines.append("file_source " + j(metadata["file_source"]))
        for key in ("event_name", "evidence_type", "narrative_context", "links", "option_trace_verified"):
            if key in metadata:
                lines.append(key + " " + j(metadata[key]))
        return lines

    lines = source_lines(data.get("source") or {})
    if "results" in data:
        lines.append("index " + data["fingerprint"])
        lines.append("search " + j({k: data[k] for k in ("terms", "mode", "order", "total_chunks",
            "total_documents", "total_source_files", "offset", "has_more", "next_offset")}))
        for label, key in (("scopes", "scope"), ("sources", "source_group")):
            lines.append("facets " + label + " (chunks/documents/files; all matches before pagination)")
            for group in data["facets"][label]:
                lines.append(f"  {group[key]}: {group['total_chunks']}/{group['total_documents']}/{group['total_source_files']}")
        for number, hit in enumerate(data["results"], data["offset"] + 1):
            lines.append(f"\n[{number}] {hit['title']} | {hit['scope']} | event={hit['event_id']}")
            lines.append(f"file {hit['source_file']} | sha256={hit['source_sha256']}")
            lines.extend(context_lines(hit))
            hint = hit["read_hint"]
            lines.append(f"read --id {hint['id']} --around {hint['around']} --context {hint['context']} --limit {hint['limit']}")
            lines.append(f"blocks {hit['block_start']}..{hit['block_end_exclusive']} (end exclusive); excerpt:")
            lines.append(hit["snippet"])
        lines.append("\nExcerpts locate evidence, not complete scenes. Use read; ranking is not truth/canon confidence.")
    else:
        lines.append(f"{data['title']} | {data['scope']} | doc_id={data['doc_id']}")
        lines.append(f"file {data['source_file']} | sha256={data['source_sha256']}")
        lines.extend(context_lines(data))
        lines.append("window " + j({k: data[k] for k in ("block_start", "block_end_exclusive", "total_blocks",
            "has_previous", "has_more", "next_start")}))
        if data.get("requested_locator") is not None:
            lines.append("locator " + j({"requested": data["requested_locator"], "matching_blocks": data["locator_matches"]}))
        for block in data["blocks"]:
            tags = {k: v for k, v in block.items() if k not in ("text", "raw_text", "speaker", "locator", "index") and v is not None}
            header = f"[{block['index']} | {block['locator']}]"
            if tags:
                header += " " + j(tags)
            if block.get("speaker"):
                header += " " + block["speaker"]
            lines.extend((header, block["text"]))
            if block.get("raw_text") is not None and block["raw_text"] != block["text"]:
                lines.append("raw_text " + j(block["raw_text"]))
        if data.get("sibling_records_sample"):
            lines.append("sibling_records_sample (not exhaustive; use --format json for full record metadata)")
            for record in data["sibling_records_sample"]:
                lines.append(j(record))
        if data["has_more"]:
            lines.append(f"continue: read --id {data['doc_id']} --start {data['next_start']}")
    return "\n".join(lines)


def outline_command(args):
    """Ordered official synopses with exact source references; no model calls."""
    root, con, meta = open_index(args)
    try:
        event = next((e for e in meta["events"] if e["event_id"] == args.event), None)
        if event is None:
            raise CorpusError("未找到这个 event ID。先用 catalog 查询实际目录，不要猜活动代码。")
        rows = list(con.execute(
            "SELECT id,path,pointer,scope,title,source_sha256,meta FROM documents WHERE event_id=?",
            (args.event,)))
        by_story = collections.defaultdict(list)
        for row in rows:
            metadata = json.loads(row["meta"])
            key = metadata.get("story_key", "").casefold()
            by_story[key].append(row)
        checked = set()

        def reference(row):
            if row["path"] not in checked:
                if digest_file(root / row["path"]) != row["source_sha256"]:
                    raise CorpusError("概要源文件内容已改变，请重建索引后重新读取。")
                checked.add(row["path"])
            metadata = json.loads(row["meta"])
            return {"doc_id": row["id"], "source_file": row["path"],
                    "locator": row["pointer"], "source_sha256": row["source_sha256"],
                    "file_source": metadata.get("file_source")}

        def summaries(records, scope):
            merged = {}
            # Embedded official storyInfo precedes the root lookup table.
            for row in sorted(records, key=lambda r: (r["path"] == "storyinfo.json", r["path"])):
                if row["scope"] != scope:
                    continue
                ref = reference(row)
                blocks = json.loads(con.execute("SELECT blocks FROM documents WHERE id=?", (row["id"],)).fetchone()[0])
                text = "\n".join(b["text"] for b in blocks)
                entry = merged.setdefault(text, {"text": text, "sources": []})
                entry["sources"].append(ref)
            return list(merged.values())

        selected = event["stories"][args.offset:args.offset + args.limit]
        items = []
        for ordinal, story in enumerate(selected, args.offset + 1):
            records = by_story[story["story_key"].casefold()]
            synopses = summaries(records, "synopsis")
            bodies = [dict(row) for row in records if row["pointer"] == "" and
                      row["scope"] not in {"synopsis", "summary", "metadata"}]
            items.append({"ordinal": ordinal, "title": story["title"], "story_key": story["story_key"],
                "sort": story["sort"], "official_synopses": synopses,
                "synopsis_status": "missing" if not synopses else "conflicting" if len(synopses) > 1 else "available",
                "generated_summaries": summaries(records, "summary") if args.include_generated else [],
                "body_available": bool(bodies),
                "documents": [{"doc_id": r["id"], "scope": r["scope"], "source_file": r["path"],
                               "read_hint": {"id": r["id"], "start": 0}} for r in bodies]})
        more = args.offset + len(items) < len(event["stories"])
        return {"kind": "outline", "source": meta["source"],
                "fingerprint": meta["coverage"]["fingerprint"],
                "event_id": event["event_id"], "event_name": event["name"],
                "total_stories": len(event["stories"]), "offset": args.offset, "has_more": more,
                "next_offset": args.offset + len(items) if more else None, "stories": items,
                "generated_event_summaries": summaries(by_story[""], "summary")
                    if args.include_generated and args.offset == 0 else [],
                "note": "按官方目录顺序展示；一条是一个剧情段落，可能是行动前、后或纯剧情。"
                        "官方简介仅导航，缺失不自动改用生成摘要；不同简介保留并标记冲突。"
                        "目录外的额外文本和其他数据表仍须通过 find/search 补查。"}
    finally:
        con.close()


def navigation_response(data):
    """Compact source cards and ordered outlines, retaining unabridged synopses."""
    def j(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    source = data["source"]
    lines = ["source " + j({k: source[k] for k in
             ("mode", "repo", "release", "version", "revision", "commit", "source_url") if k in source}),
             "index " + data["fingerprint"]]
    if data["kind"] == "find":
        lines.append("find " + j({k: data[k] for k in ("terms", "mode", "total_groups",
                     "total_chunks", "total_documents", "offset", "has_more", "next_offset")}))
        for number, group in enumerate(data["groups"], data["offset"] + 1):
            lines.append(f"\n[{number}] {group['title']} | {group['group_id']}")
            lines.append(f"scopes={','.join(group['scopes'])} | text_documents={group['text_documents']}"
                         f" | navigation_documents={group['navigation_documents']} | chunks={group['total_chunks']}")
            if group["outline_hint"]:
                lines.append("outline --event " + group["outline_hint"]["event"])
            for hit in group["samples"]:
                lines.append(f"sample: {hit['title']} | {hit['scope']} | {hit['metadata']['evidence_type']}")
                lines.append(f"file {hit['source_file']} | locator={hit['record_pointer']} | sha256={hit['source_sha256']}")
                if hit["metadata"].get("file_source"):
                    lines.append("file_source " + j(hit["metadata"]["file_source"]))
                lines.append(f"read --id {hit['doc_id']} --around {hit['block_start']}")
                lines.append(hit["snippet"])
    else:
        lines.append(f"outline {data['event_name']} | event={data['event_id']}")
        lines.append("page " + j({k: data[k] for k in
                     ("total_stories", "offset", "has_more", "next_offset")}))

        def append_summaries(label, summaries):
            for entry in summaries:
                lines.extend((label + ":", entry["text"]))
                for ref in entry["sources"]:
                    lines.append("reference " + j(ref))

        append_summaries("生成的活动摘要（仅导航）", data["generated_event_summaries"])
        for story in data["stories"]:
            lines.append(f"\n[{story['ordinal']}] {story['title']} | key={story['story_key']}")
            lines.append("official_synopsis=" + story["synopsis_status"])
            append_summaries("官方简介", story["official_synopses"])
            append_summaries("生成摘要（仅导航）", story["generated_summaries"])
            if not story["body_available"]:
                lines.append("正文缺失或未索引；不能据此断定剧情没有答案。")
            for doc in story["documents"]:
                lines.append(f"read --id {doc['doc_id']} --start 0 | {doc['source_file']}")
        if data["has_more"]:
            lines.append(f"continue: outline --event {data['event_id']} --offset {data['next_offset']}")
    lines.append("\n" + data["note"])
    return "\n".join(lines)


def catalog_command(args):
    root, con, meta = open_index(args)
    query = (args.query or "").casefold()
    events = [e for e in meta["events"] if query in ((e["name"] or "") + " " + e["event_id"]).casefold()
              and (not args.event or e["event_id"] == args.event)]
    out = []
    for event in events[args.offset:args.offset + args.limit]:
        e = dict(event)
        e["story_count"] = len(e["stories"])
        if args.event:
            for s in e["stories"]:
                prefix = "gamedata/story/" + s["story_key"]
                s["documents"] = [dict(r) for r in con.execute(
                    "SELECT id,scope,path FROM documents WHERE lower(path) IN (lower(?),lower(?)) AND pointer=''",
                    (prefix + ".json", prefix + ".txt"))]
        else:
            del e["stories"]
        out.append(e)
    con.close()
    return {"source": meta["source"], "total": len(events), "events": out,
            "has_more": args.offset + len(out) < len(events),
            "note": "published_timestamp 是发布元数据，不是故事发生时间；目录之外也可能有文本。"}


def entities_command(args):
    root, con, meta = open_index(args)
    q = args.query.casefold()
    found = [e for e in meta["entities"] if any(q in str(e.get(k) or "").casefold() for k in ("id", "name", "appellation"))]
    con.close()
    return {"source": meta["source"], "total": len(found), "entities": found[args.offset:args.offset + args.limit],
            "has_more": args.offset + args.limit < len(found),
            "note": "只包含角色数据表条目，不是全 NPC/历史人物名单；代号、称号和英文栏不能自动视为本名。"}


def status(args):
    root = get_root(args, required=False)
    if root is None:
        return {"ready": False, "cache": str(args.cache), "next": "prepare"}
    path = db_path(root, args.cache)
    result = {"ready": False, "root": str(root), "cache": str(args.cache), "source": source_info(root)}
    if path.exists():
        con = sqlite3.connect(path)
        meta = {k: json.loads(v) for k, v in con.execute("SELECT key,value FROM metadata")}
        con.close()
        result["ready"] = meta.get("schema") == SCHEMA and meta.get("stamp") == stamp(root)
        result["coverage"] = meta.get("coverage")
        result["built_at"] = meta.get("built_at")
    if not result["ready"]:
        result["next"] = "index"
    return result


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="已有仓库根或 zh_CN 目录；默认 ARKNIGHTS_DATA 或活动缓存")
    parser.add_argument("--cache", type=Path, default=default_cache(), help="独立缓存目录")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="检查本地版本与覆盖；不联网")
    prep = sub.add_parser("prepare", help="无语料时下载；有语料时建立或复用索引")
    prep.add_argument("--update", action="store_true", help="检查新发布；不修改 --root 指向的用户仓库")
    prep.add_argument("--repo", default=DEFAULT_REPO)
    prep.add_argument("--release", help="固定 data-* 或 datarev-* 发布标签")
    prep.add_argument("--archive", help="导入已下载的 zh_CN.zip")
    prep.add_argument("--sha256", help="本地导入包的可信 SHA-256")
    prep.add_argument("--no-supplement", action="store_true", help="只使用主发布包，不补充独立来源的历史肉鸽外置文本")
    prep.add_argument("--supplement-ref", help="补充源固定的 40 位提交 SHA，用于复现组合语料版本")
    sub.add_parser("index", help="重建变更后的本地索引")
    s = argparse.ArgumentParser(add_help=False)
    s.add_argument("query")
    s.add_argument("--term", action="append")
    s.add_argument("--mode", choices=["any", "all", "lexical"], default="any")
    s.add_argument("--order", choices=["relevance", "source"], default="relevance",
                   help="默认按词覆盖、正文/叙事结构及来源多样性排序；source 保留索引入库顺序供审计")
    s.add_argument("--scope", action="append", choices=sorted(set(TABLE_SCOPES.values()) | {"story", "memory", "summary", "synopsis"}))
    s.add_argument("--event")
    s.add_argument("--path", help="源文件路径子串过滤")
    s.add_argument("--include-navigation", action="store_true")
    s.add_argument("--limit", type=int, default=12)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--format", choices=["compact", "json"], default="compact",
                   help="默认紧凑证据文本；json 保留全部结构化元数据，供脚本处理")
    sub.add_parser("search", parents=[s], help="定位正文片段；默认不查概要")
    f = sub.add_parser("find", parents=[copy.deepcopy(s)], help="查找候选来源：默认兼查概要和原文，按来源分组后分页")
    f.set_defaults(group_sources=True, include_navigation=True)
    f.add_argument("--text-only", dest="include_navigation", action="store_false", help="只查正文及叙事表")
    o = sub.add_parser("outline", help="按官方剧情顺序浏览关卡概要和正文入口")
    o.add_argument("--event", required=True)
    o.add_argument("--include-generated", action="store_true", help="额外显示标明来源的生成摘要；不代替官方简介")
    o.add_argument("--limit", type=int, default=20)
    o.add_argument("--offset", type=int, default=0)
    o.add_argument("--format", choices=["compact", "json"], default="compact")
    r = sub.add_parser("read", help="读取完整原文窗口并保留引用位置")
    r.add_argument("--id", required=True)
    window = r.add_mutually_exclusive_group()
    window.add_argument("--start", type=int, default=0)
    window.add_argument("--around", type=int, help="用搜索结果 block_start 作锚点展开，不是原始 node_id")
    window.add_argument("--locator", help="原文精确定位，例如 /storyList/42 或 line:7；匹配多块时从第一块展开")
    r.add_argument("--context", type=int, default=12, help="around 锚点前最多保留的块数；limit 为含锚点的总窗口上限")
    r.add_argument("--limit", type=int, default=60)
    r.add_argument("--format", choices=["compact", "json"], default="compact")
    c = sub.add_parser("catalog", help="查找活动；指定 --event 后按官方顺序列出正文")
    c.add_argument("query", nargs="?", default="")
    c.add_argument("--event")
    c.add_argument("--limit", type=int, default=25)
    c.add_argument("--offset", type=int, default=0)
    e = sub.add_parser("entities", help="查找干员中文名、内部 ID、英文栏")
    e.add_argument("query")
    e.add_argument("--limit", type=int, default=25)
    e.add_argument("--offset", type=int, default=0)
    return parser


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = make_parser()
    args = parser.parse_args()
    args.cache = args.cache.expanduser().resolve()
    if getattr(args, "limit", 1) < 1 or getattr(args, "limit", 1) > 500:
        parser.error("limit 必须为 1..500")
    if (getattr(args, "offset", 0) < 0 or getattr(args, "start", 0) < 0
            or (getattr(args, "around", None) is not None and args.around < 0)
            or getattr(args, "context", 0) < 0):
        parser.error("起始位置不能为负数")
    try:
        handlers = {"status": status, "prepare": prepare, "index": lambda a: build_index(get_root(a), a.cache),
                    "search": search, "find": search, "outline": outline_command,
                    "read": read_document, "catalog": catalog_command, "entities": entities_command}
        result = handlers[args.command](args)
        if getattr(args, "format", "json") == "compact":
            print(compact_response(result))
        else:
            emit(result)
    except (CorpusError, OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as exc:
        emit({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
