# 资料访问与版本

## 选择可用路径

| 当前能力 | 做法 |
|---|---|
| 有本地文本和 Python | 指定 `--root`，建立或复用独立索引；原始仓库只读 |
| 无文本，有 Python 3.10+ 和网络 | `status` 后按需 `prepare`，自动取得公开数据并建索引 |
| 只有网页工具 | 查实际可访问的目录、简介和原文页面；仍沿新线索迭代 |
| 已连接剧情 MCP | 先发现真实接口、版本和覆盖范围，再用其查目录、概要和原文 |
| 无可读资料 | 说明访问不足，不把模型记忆包装成检索结果 |

不要求用户先克隆仓库，也不自动安装 Python、部署服务器或购买服务。工具具体参数见 [工具说明](tools.md)。

## 数据来源

- 主数据工厂和正式 Release：[3aKHP/arknights-data-pipeline](https://github.com/3aKHP/arknights-data-pipeline)。
- 兼容分发及外置肉鸽补充：[3aKHP/ArknightsStoryJson](https://github.com/3aKHP/ArknightsStoryJson)。
- 历史来源：[050644zf/ArknightsStoryJson](https://github.com/050644zf/ArknightsStoryJson)、[Kengxxiao/ArknightsGameData](https://github.com/Kengxxiao/ArknightsGameData)。这些不是自动替换主源的候选。

获取器沿用已核对的 `prts-mcp-data/v1` 清单契约，选择正式 `data-*` / `datarev-*` 发布，跳过图片包、草稿和预发布；不直接假定 `/releases/latest` 就是文本包。下载检查清单、资产大小和 SHA-256，安全解压，不执行下载包中的代码。契约变化时报错，不随机换镜像。

主包可能没有独立保存的肉鸽 TXT。自动获取默认尝试从兼容仓补缺：先固定提交，再按 Git blob 校验内容，逐文件保存独立来源。`--no-supplement` 可以关闭；用户指定本地目录或离线 ZIP 时不自动补写。补充失败不妨碍使用已有文本，但依赖缺失材料的问题必须说明限制。

## 固定版本与缓存

首次检查 `status` / `prepare` 的版本和覆盖报告，尤其是缺表、解析失败、`rogue_external_records` 未解决引用及 `source.supplement`。目录完整不代表所有资料类型完整。

已有语料时 `prepare` 默认不联网；用户问最新才用独立缓存的 `prepare --update`。指定 `--root` 不会更新该仓库。一次回答固定主发布和补充提交，不把不同来源冒充同一版本；复现时同时指定 `--release` 与 `--supplement-ref`。

默认缓存目录名为 `arknights-story-navigator`，按平台放在用户缓存目录，不随 skill 分享。支持 `--cache`、`ARKNIGHTS_NAVIGATOR_CACHE`，兼容显式设置的 `ARKNIGHTS_STORY_CACHE`；已有语料可用 `ARKNIGHTS_DATA`。新快照成功后再激活，失败保留旧版，不自动清理旧缓存。自带目录缺版本信息时标为未核实本地版本，文件修改时间不能证明剧情版本。

## 网页与 MCP

网页可从 GitHub 的真实目录、已验证的原文链接，或 PRTS 的剧情原文转载页进入。综合百科条目和搜索摘要只导航，回查引用；注意分页、折叠和截断。不要根据发布包路径伪造 GitHub raw 链接；无法给单文件链接时附发布页、包内路径和位置。

MCP 是可选能力，不是假定已存在的公共服务。参考项目为 [PRTS-MCP](https://github.com/3aKHP/prts-mcp)，但工具名和参数以宿主实际提供的为准。确认其是否覆盖模组、档案、肉鸽外置记录、关卡简介，使用返回的 ID，不猜接口或键。缺能力时可用已可用的另一种访问方式补查，记录来源差异。

无网络不等于不可使用：已取得的本地语料可离线查询。反之，只能浏览网页时不能声称完成全库穷尽；漫画、设定集、PV 和未覆盖更新按需要另查，不能把访问失败判为剧情未知。
