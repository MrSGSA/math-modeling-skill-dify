# stdio MCP 配置参数

当前 Agent 应优先读取其客户端的本地帮助或官方配置说明，再应用本页参数。不要假设所有客户端都使用相同命令、作用域或 JSON 字段。

## 服务参数

- 名称：`math_modeling_knowledge`
- 传输：`stdio`
- command：`dify_knowledge_bridge/.venv` 中 Python 的绝对路径
- args：`dify_knowledge_bridge/mcp_server.py` 的绝对路径
- cwd：`dify_knowledge_bridge` 的绝对路径
- 密钥：由服务从同目录 `.env` 读取，不要把密钥写进共享 MCP 配置

等价的通用配置形状：

```json
{
  "mcpServers": {
    "math_modeling_knowledge": {
      "type": "stdio",
      "command": "<ABSOLUTE_PATH_TO_VENV_PYTHON>",
      "args": ["<ABSOLUTE_PATH_TO_MCP_SERVER.PY>"],
      "cwd": "<ABSOLUTE_PATH_TO_DIFY_KNOWLEDGE_BRIDGE>"
    }
  }
}
```

不同客户端可能使用 `servers`、`mcp_servers` 或命令行注册，不得未经核验直接写入某个猜测路径。

## 客户端适配

- Codex：先运行 `codex mcp --help`；能选择作用域时优先项目级，否则说明将写入用户级配置。
- Claude Code：先运行 `claude mcp --help`；优先 `project` 作用域，不照搬 Codex 的注册命令。
- 其他 Agent：检查是否支持 stdio MCP。支持时按上面的 command/args/cwd 配置；不支持时直接调用 `kb_bridge.py query --json`。

## 验证顺序

1. `python -m unittest test_kb_bridge.py`
2. `python kb_bridge.py doctor`
3. 启动 `mcp_server.py` 并检查工具发现
4. 调用 `math_modeling_knowledge_status`
5. 用 `core` 做文字检索；如已配置多模态库，再用 `multimodal` 验证图片内容块

只有真实调用通过后才向用户报告启用成功。
