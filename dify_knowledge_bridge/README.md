# Dify 九库检索桥

本目录把 8 个文本知识库和 1 个多模态知识库统一为一个 stdio MCP 服务。它不绑定 Codex；任何支持 stdio MCP 的客户端均可连接，其他 Agent 也可直接调用 `kb_bridge.py`。

## 配置文件

复制密钥模板：

```powershell
Copy-Item .env.example .env
```

填写：

```text
DIFY_KNOWLEDGE_API_KEY=你的知识库Service API密钥
```

然后编辑 `knowledge_bases.yaml`：

- `dify.base_url`：Dify Service API 地址，通常以 `/v1` 结尾；
- `dify_name`：Dify 页面中的真实知识库名称；
- `dataset_id: auto`：按名称自动发现 ID；
- `enabled: false`：暂时禁用尚未建立的库。

默认九库分层：

- `core`：建模算法、编程实现、竞赛规则、领域知识、论文写作；
- `experience`：优秀案例、错题本、实践复盘；
- `multimodal`：多模态原图与图片上下文；
- `all`：全部 8 个文本库，不包含多模态库。

## 创建环境与测试

Windows：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest test_kb_bridge.py
.\.venv\Scripts\python .\kb_bridge.py doctor
```

macOS/Linux：

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m unittest test_kb_bridge.py
./.venv/bin/python ./kb_bridge.py doctor
```

## 直接查询

```powershell
.\.venv\Scripts\python .\kb_bridge.py query "回焊炉炉温曲线优化有哪些模型？" --json
```

完整结果默认写入 `last_result.json`，该文件不会提交到 Git。

## MCP

阅读 `MCP_SETUP.md`。Agent 应先检测当前客户端的 MCP 命令与配置格式，再把本目录的 `.venv` Python 和 `mcp_server.py` 注册为 `math_modeling_knowledge`。

`install_or_repair_codex_mcp.ps1` 与中文 CMD 入口仅作为 Windows + Codex 的兼容辅助，不是本套件的主入口。
