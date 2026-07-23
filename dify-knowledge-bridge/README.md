# Dify 多知识库统一检索测试

本目录先验证一件事：用一把 Dify 知识库 Service API 密钥，并行查询 8 个文本库和 1 个多模态库。验证通过后，`kb_bridge.py` 会作为 Codex MCP 服务的检索内核，不需要重写 9 套 API。

## 第一次使用

1. 在 Dify 顶部进入“知识库 → Service API → API 密钥”，创建或复制一把知识库 API 密钥。
2. 编辑本目录的 `.env`：

   ```text
   DIFY_KNOWLEDGE_API_KEY=在这里粘贴密钥
   ```

3. 双击 `检查连接.cmd`。
4. 新建“数学建模-实践复盘库”，把其 ID 填入 `knowledge_bases.yaml` 并将 `enabled` 改为 `true`；随后应看到 9 行 `[OK]`。如果某个库显示“未找到”，把相应的 `dify_name` 改成 Dify 页面显示的准确名称。
5. 双击 `测试全部知识库.cmd`，输入一个确实可能同时涉及算法、论文和图表的问题，例如：

   ```text
   回焊炉炉温曲线优化可采用哪些模型，目标函数如何设置，并找出相关曲线图？
   ```

完整结果会写入 `last_result.json`。其中：

- `text_hits`：来自 8 个文本库，供回答主体使用；
- `multimodal_context_hits`：多模态库中与图片绑定的父块上下文；
- `image_hits`：多模态库实际返回的图片附件及 URL；
- `errors`：某个库的独立错误，不会掩盖其他库的成功结果。

针对 Dify 1.14.2，脚本还会自动处理一个接口差异：当 `/retrieve` 命中了带图父块、却返回空 `files` 时，会并行读取对应 segment 详情中的 `attachments`，因此无需人工查图。

## 命令行用法

```powershell
cd '<仓库目录>\dify-knowledge-bridge'
python .\kb_bridge.py doctor
python .\kb_bridge.py query '回焊炉炉温曲线优化有哪些建模方案和相关图表？'
python .\kb_bridge.py query '问题文本' --json
```

## YAML 的作用

YAML 不是用来保存知识内容或密钥的，只定义：

- 哪些 Dify 知识库参与检索；
- 每个库是正文库还是图片补充库；
- 各库的 Top K、检索方式和阈值；
- 汇总后最多保留多少条文字与图片。

`dataset_id: auto` 会按名称自动发现 ID，因此 Dify 中增加文档不需要修改本地配置。只有知识库改名、增加新库或改变检索策略时才需要改 YAML。

## 接入 Codex MCP

双击 `安装或修复Codex_MCP.cmd`。它会：

1. 在本目录创建隔离的 `.venv`；
2. 安装稳定版 MCP Python SDK；
3. 注册全局 Codex MCP `math_modeling_knowledge`；
4. 真正启动 MCP 客户端，测试工具发现、9 库查询和图片内容块。

安装完成后必须完全退出并重新打开 Codex，新线程中才会出现：

- `search_math_modeling_knowledge`：统一查询 9 库，并可直接把相关图片交给 Codex 视觉理解；
- `math_modeling_knowledge_status`：只读检查连接和 9 库匹配状态。

`search_math_modeling_knowledge` 默认使用 `knowledge_scope=core`，避免经验资料挤占当前题的主体证据：

- `core`：建模算法、编程实现、竞赛规则、领域知识、论文写作5个文本库；
- `experience`：优秀案例、错题本、实践复盘3个经验文本库；
- `all`：8个文本库总览，不包含多模态库；
- `multimodal`：只查询多模态库，在有明确图片需求时使用。

推荐先查 `core` 或 `experience`，再把命中的来源文档名、图号或图表主题带入 `multimodal` 定向查图。这样不会借多模态索引绕过文本分层。`include_images=false` 只是不返回图片内容块；它不改变所选范围。

Codex 配置只保存 Python 与 MCP 服务脚本路径。Dify 密钥仍只保存在本目录 `.env` 中。
