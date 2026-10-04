# 明日方舟剧情导航 Skill

独立的轻量导航版：先找来源，优先浏览关卡概要，再读原文，围绕未解决的问题跨文本迭代。旧版 `arknights-story` 不会被本包替换。

每道剧情问题必须实际检索并读取相关原文，不能只凭模型记忆回答；无法取得证据时说明限制。这是明确的 skill 指令，不是宿主层面的程序拦截，不能保证所有模型都会遵守。

## 使用与分享

把整个 `arknights-story-navigator` 文件夹交给支持本地 skill 的应用，放入该应用配置的技能目录。按宿主说明加载；不支持技能目录的模型需要读取 `SKILL.md`，并能够按需访问参考文件和资料工具。仅粘贴入口、却不给文件或联网能力，无法实现完整检索。

示例请求：

> 使用 arknights-story-navigator，先查看相关作品概要，再核对原文，回答我的剧情问题并给出出处。

`agents/openai.yaml` 仅是 Codex 的可选界面信息，其他模型可忽略。若同时装了旧版和新版，做对照时应只启用被测版本，避免两套指令一起加载。

## 没有本地文本也可开始

在本文件夹运行（命令也可能是 `python3` 或 `py -3`）：

```sh
python scripts/corpus.py status
python scripts/corpus.py prepare
python scripts/corpus.py catalog "活动名"
```

已有数据可用 `python scripts/corpus.py --root "/path/to/repo" prepare`。首次下载及索引需要网络、Python 3.10+ 和足够磁盘；数据和索引可能达到 GB 量级。之后可以离线检索。没有 Python 时按 `references/access.md` 使用网页或现成 MCP，不自动安装系统组件。

分享完整文件夹或同名 ZIP；不用附带语料、索引、测试答案、API 密钥或本机绝对路径。包内脚本只有标准库依赖，不调用模型 API。社区文本的权利归相应权利人，校验下载完整性不等于认证全部叙事内容。

## 范围与方法

支持主线、活动、档案、密录、模组、语音、时装及集成战略等文本。工具提供定位和读取，模型判断意义；不自动建立知识图谱、不自动认定正史、不能保证穷尽全部游戏外出版物。

方法借鉴：[PageIndex](https://github.com/VectifyAI/PageIndex) 的层级导航、[LlamaIndex](https://developers.llamaindex.ai/python/framework/integrations/retrievers/auto_vs_recursive_retriever/) 的概要到原文映射、[GraphRAG DRIFT](https://microsoft.github.io/graphrag/query/drift_search/) 的迭代查找。只借鉴组织方式，不依赖这些框架；本工具基于独立保存的既有本地语料工具扩展。

本版没有预装剧情测试题或答案；程序检查不等于模型正确率验证。测试和交付检查记录保留在分享包外。
