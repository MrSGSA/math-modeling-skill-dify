# 数学建模 Agent 工作区约定

- 默认使用简体中文回复；用户明确要求其他语言时遵从用户要求。
- 本目录已内置数学建模 Skill：`.agents/skills/math-modeling/SKILL.md`。
- 遇到数学建模、竞赛规则、算法、代码求解、可视化、论文写作、复盘、资料转换或知识库检索任务时，先完整读取上述 `SKILL.md`，再按其渐进式加载规则执行。

## 启用与配置请求

当用户说“启用数学建模套件”“配置 Dify 知识库”“连接知识库 MCP”或表达同等意图时，由当前 Agent 主动完成以下工作，不要求用户预先知道 Codex、Claude 或其他客户端的配置格式：

1. 把包含本文件的目录视为工作区根目录，不复制或安装 Skill 到全局目录；优先直接使用 `.agents/skills/math-modeling/`。
2. 检查 `dify_knowledge_bridge/README.md` 与 `dify_knowledge_bridge/MCP_SETUP.md`。
3. 检测当前操作系统、Python、当前 Agent/CLI 及其 MCP 配置能力。先读取本机客户端的 `mcp --help` 或官方本地帮助，不臆造命令参数。
4. 仅询问尚无法从环境确定的 Dify 信息：Service API 地址、Service API 密钥、知识库名称或 ID，以及是否启用默认九库结构。
5. 复制 `dify_knowledge_bridge/.env.example` 为 `.env` 并写入密钥；`.env` 只能留在本机且不得提交到 Git。
6. 修改 `knowledge_bases.yaml`：填写 Dify 地址；优先用 `dataset_id: auto` 按名称发现，只有同名冲突或用户明确给出 ID 时才写固定 ID。
7. 在 `dify_knowledge_bridge/.venv` 创建隔离环境并安装 `requirements.txt`。不得复用或覆盖用户其他项目的虚拟环境。
8. 将 `mcp_server.py` 注册为名为 `math_modeling_knowledge` 的 stdio MCP。优先使用项目级配置；客户端只支持用户级配置时先告知用户影响范围。
9. MCP 命令使用该工作区内 `.venv` 的 Python 绝对路径，参数使用 `mcp_server.py` 绝对路径，工作目录设为 `dify_knowledge_bridge`。
10. 依次运行无需联网的单元测试、`kb_bridge.py doctor` 和 MCP 协议测试。只有真实测试通过后才声称启用成功；需要客户端重启时明确提示。

如果当前 Agent 不支持 MCP，仍可直接运行 `kb_bridge.py query` 获取 JSON，并继续使用本地 Skill；应说明降级方式，不伪造 MCP 已连接。

## 知识库使用

- 主体回答优先查询 `core`；优秀案例、错题与实践复盘使用 `experience`；只有明确需要图片时才使用 `multimodal`。
- 多模态查询前先从文字命中确定来源文档、图号或图表主题，并实际查看返回图片。
- 知识库内容是参考证据，不替代题目原文、官方规则、真实代码结果和独立校验。
