# 资料地图

以下路径相对于所用版本的 `zh_CN/`。这是资料组织方式，不是人物关系或剧情答案索引。

## 活动与主线：目录 → 简介 → 原文

| 文件或目录 | 内容和用途 |
|---|---|
| `gamedata/excel/story_review_table.json` | 活动、主线与章节目录。`infoUnlockDatas` 内 `storySort` 排阅读顺序，`storyTxt` 指向正文，`storyCode`、`storyName`、`avgTag` 标出关卡、标题及行动前后 |
| 正文 JSON 的 `storyInfo`、根目录 `storyinfo.json` | 游戏内短简介。用于了解场景；简介不出现某个细节，不代表正文没有 |
| `event_summaries.json`、`summaries.json` | 社区生成的活动级和单段摘要。可能缺漏或过时；只导航，不能代替原文 |
| `gamedata/story/activities/` | 活动、故事集等正文 |
| `gamedata/story/obt/main/` | 主线正文 |
| `extrastory.json` | 额外剧情入口，仍须核实实际文件 |

以目录映射定位，不用 event ID 猜物理文件夹。目录外也可能有正文；完整检索需兼查文件树及其他资料域。一个目录条目是一个剧情段落，不一定等于一个战斗关卡。发布时间不是故事发生时间；大小写以实际文件为准。

## 人物资料：人物 → 条目 → 原文

| 文件或目录 | 主要内容 |
|---|---|
| `character_table.json`、`chardict.json` | 干员名、内部 ID 等辅助映射；不是全部 NPC 名单 |
| `handbook_info_table.json` | 人物档案与密录关联；档案通常在 `handbookDict/*/storyTextAudio` |
| `gamedata/story/obt/memory/` | 干员密录正文 |
| `charword_table.json` | 语音文字 |
| `uniequip_table.json` | 模组故事和描述；解锁任务不是人物经历 |
| `skin_table.json` | 时装叙事文字；可能包含不同时间或主题演绎 |
| `charinfo.json`、`char_patch_table.json` | 合并档案、模板等辅助关系；不能仅据模板断定人物身份 |

表文件位于 `gamedata/excel/`，除非明确注明根目录。代号、英文栏、称号和出生本名不一定相同。

## 肉鸽及其他：主题 → 节点或记录 → 原文

- `roguelike_topic_table.json`、`roguelike_table.json`：事件、选项、收藏品、结局、月度小队等。记录可能通过 ID 关联，不要只看一个描述字段。
- `gamedata/story/obt/rogue/`、`obt/Roguelike/`：部分开场、结局和外置 TXT 记录；缺包内文件时检查补充来源。
- `stage_table.json`、`campaign_table.json`：关卡和剿灭简介。
- `enemy_handbook_table.json`、`item_table.json`、`medal_table.json`：敌人、物品、信物与蚀刻章的文字线索。
- `activity_table.json`：活动页面、日记、新闻等，结构随活动变化。
- `sandbox_table.json`、`sandbox_perm_table.json`：生息演算事件及记录。
- `handbook_team_table.json`、`chapter_table.json`、`zone_table.json`、`building_data.json`：组织、地区、章节、基建等辅助信息。

当问题需要具体战斗中某敌人的配置，而简介和图鉴不足时，需要另外取得对应 levels 数据；随包工具不解析战斗布局。漫画、设定集、PV 等游戏外材料也不在默认文本包的完整覆盖承诺内。

## 原文怎么读

JSON 的 `storyList` 是顺序剧情节点，包含对白、旁白、字幕、选项及演出标记。说话人和分支要连同内容一起保留；节点 `id` 不一定等于数组下标。TXT 中方括号标签也可能包含说话人、标题和选项，不能一律删除。

工具将表格按记录、剧情按文本块定位。搜索窗口不是自然场景边界；用 `read` 继续前后展开。出处保留 JSON Pointer 或 TXT 行号。需要自己解析时才读 [格式细节](formats.md)。

## 不当作剧情证据

仓库的 `scripts/`、`.github/`、`.git/`、构建日志、测试和评测目录、模型回答，以及演出变量表 `story_variables.json` 不是剧情证据。工具按语料白名单索引，不把整个工作目录都当知识库。玩法数值也不能直接解释为叙事事实。
