# 文本解析与证据定位

## 正文 JSON

顶层常见 `lang`、`eventid`、`eventName`、`entryType`、`storyCode`、`avgTag`、`storyName`、`storyInfo`、`storyList`、`OPTIONTRACE`。不能因某项为空而丢掉全文。

`storyList` 是顺序数组。单节点有 `id`、`prop`、`attributes`，有时含 `figure_art`、`targetLine`、`endOfOpt`。`id` 通常关联原始行；引用同时保存数组位置，避免将两种编号混淆。

| prop | 提取和保留 |
|---|---|
| `name` | `attributes.name`、`content`；name 为空可能是叙述 |
| `multiline` | 逐段 content；joined 可能重复前面片段，不要再拼一次 |
| `Subtitle`、`Sticker` | text/content；可能是旁白、信件、字幕，不默认归给当前立绘 |
| `animtext` | 时间、地点或其他字幕；富文本标签可影响段落顺序 |
| `Decision` | options 可以是分号分隔字符串，也可能是数组；values 与 targetLine 一起保留 |
| `Predicate` | references、endOfOpt、targetLine；识别分支与汇合，但不自行发明已选选项 |
| 其他含 content/text 的节点 | 保留为待分类文字，避免只白名单普通对白而漏文 |
| Character/charslot、Background、Image | 场景辅助，不凭图片文件名断定匿名者身份或图中内容 |
| Delay、音量、镜头等 | 可忽略文字检索；需要演出分析时回看原节点 |

`OPTIONTRACE=false` 意味着转换器未可靠追踪所有选项，不是“没有分支”。脚本保留源标记，不保证复杂嵌套分支已语义解析；遇到矛盾应回查完整节点序列或原始 TXT。

## 原始 TXT

常见 `[prop(attr=value,...)]正文`、`[name="角色"]对白`，也有纯叙述行。属性大小写可能不同。月度对话还可用 `[Dialog(head="char_...")]`，以 character 表辅助解码，但保留原 head ID。`[Title]`、`[Div]` 标明记录和章节。

不要简单删除所有方括号：选项、说话人、标题和分支就藏在标签中。不要把所有 `//` 后面一律删除，文字或 URL 也可能含斜线。脚本只跳过独立注释行；需要精确演出时读取原文件。

富文本清洗仅用于阅读和搜索，保留 `raw_text`；`{@nickname}` 的“博士”替换是显示别名。涂黑、缺字、删节符以及未知说话人应保持其不确定性。

## 表格

记录可散布于字典、列表和嵌套结构。对 `storyText`、`voiceText`、`uniEquipDesc`、`description`、`text` 等保存原值与完整 JSON Pointer。标题继承只提供语境，不把父级标签视为该字段说话人。

肉鸽事件要关联 scene ID、choice ID、nextSceneId、结果文本和结局 ID。收藏品描述、结局简介、月度纪实并不是同一种叙事。档案的解锁条件也不是故事发生日期。

## 原子证据与阅读窗口

脚本按最多 24 个文本块或约 1,800 个字符构造检索窗口，相邻窗口重叠两个块，都是定位辅助，不声称是人工确认的场景边界。命中后用 read 扩展到足够的上下文；完整活动可按 catalog 顺序逐篇分页。

优先以连续对话、完整信件、档案段落或一个事件分支作为解释单元。不得把窗口边界当段落结尾，也不能把跨窗口同一发言重复计为两次事件。

引用形式：`作品 / 关卡·行动前后 / storyList数组位置与node_id`；表格用 `表名 / 记录ID / JSON Pointer`；TXT 用 `文件 / 行号`。发布包 URL + 包内路径也是可核查出处，不伪造网页行号。

格式参考：<https://github.com/050644zf/ASTR-Script/blob/main/parse.md>。本技能独立实现读取逻辑，不运行上游转换脚本。
