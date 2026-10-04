# 三个主要动作与获取工具

只需 Python 3.10+ 标准库，无模型 API、向量服务或第三方 Python 依赖。下列路径相对于本 skill 目录；其他目录运行时使用脚本实际路径。全局 `--root`、`--cache` 放在子命令前，各次查询使用同一组路径。

## 首次取得资料

```sh
python scripts/corpus.py status
python scripts/corpus.py prepare
python scripts/corpus.py --root "/path/to/repo" prepare
```

`status` 只检查；没有语料时 `prepare` 自动下载，有语料时建索引。`prepare --update` 检查远端更新，`index` 重建变化后的本地索引。固定发布可加 `--release <tag>`，固定补充源加 `--supplement-ref <commit>`。离线包用 `prepare --archive <zip> --sha256 <发布方提供的可信哈希>`；自算哈希不能冒充发布方验证。公共 GitHub 通常不需要密钥，已有 `GITHUB_TOKEN` 只用于 GitHub API，不输出它。

## 1. 找来源：find

```sh
python scripts/corpus.py find "人物名" --term "相关事件"
python scripts/corpus.py find "线索" --scope rogue
python scripts/corpus.py catalog "活动名"
```

`find` 默认同时检查概要、生成摘要和原文，按活动或来源表归组后分页，每组最多展示两个不同条目样例，附可继续阅读的位置和概要入口。它不是语义理解或人物出场全集。多个词默认 OR；`--mode all` 要求同一窗口都有这些词；`--mode lexical` 是中文词项排序，不是语义向量。

正文与导航命中分别计数。生成摘要命中必须回查正文；没有命中也不能排除别名或指代。`--limit`、`--offset` 对 find 控制的是来源组，不是摘录条数。`--text-only` 排除导航材料；显式 `--scope` 优先指定资料域。精确知道作品时用 `catalog` 查真实 ID，不必先做宽检索。

## 2. 看概要：outline

```sh
python scripts/corpus.py outline --event <catalog返回的ID>
python scripts/corpus.py outline --event <ID> --offset 20 --limit 20
python scripts/corpus.py outline --event <ID> --include-generated
```

按官方剧情顺序显示标题、行动前后、完整官方简介和正文入口，默认每页 20 段。`has_more` 表示还有后续；缺简介或正文会明确标记。相同简介合并展示但保留来源；两份官方简介内容不同则同时保留并标记冲突。`--include-generated` 才额外显示生成摘要，始终标为导航，绝不填充“官方简介”字段。

`outline` 面向有目录映射的活动和主线，不是全部资料的树。人物资料、模组和肉鸽记录用 `find` / `search --scope ...` 浏览标题与来源，再进入具体记录。正文目录也不自动包含相关 Excel 表内的所有文字。

## 3. 读原文：read；全文定位：search

```sh
python scripts/corpus.py search "精确词" --event <ID>
python scripts/corpus.py search "名字" --scope dossier --scope module
python scripts/corpus.py read --id <doc_id> --around <block_start>
python scripts/corpus.py read --id <doc_id> --start 0 --limit 60
python scripts/corpus.py read --id <doc_id> --locator "/storyList/42"
```

优先复制工具返回的 read 参数，前面补相同的 Python、脚本和全局路径。`--around` 展开命中块，`--context` 设置向前的块数，`--limit` 限制总窗口；`--start`、`--around`、`--locator` 互斥。继续读时使用 `next_start`。块索引不是原始 node ID；精确位置使用 JSON Pointer 或 TXT 的 `line:N`。

`search` 默认排除概要和元数据，`--include-navigation` 可纳入；`--event`、`--scope`、`--path` 缩小范围。常用资料域为 story、memory、dossier、voice、module、skin、rogue、stage、enemy、item、activity、sandbox。表内记录保留记录路径和显式关联 ID；相邻记录样例不是全集。`entities` 只提供角色表的名字候选，不含所有 NPC 和别名。

## 输出和限制

find、outline、search、read 默认紧凑文本；加 `--format json` 得结构化结果。原文和简介不由模型改写；搜索摘录可能截断，需 read。其他命令返回 JSON，进度在 stderr，出错退出码为 2。

排序和命中数量不代表事实可信度、出现次数或正史地位。索引检测源目录变化会要求重建；读取原文和概要还检查源文件哈希。保留 `file_source` 时优先使用该独立来源，不把补充 TXT 归给主发布包。引文应附作品、章节或字段和原文定位；生成摘要不是最终证据。
