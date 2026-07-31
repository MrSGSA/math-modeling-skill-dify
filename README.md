# 数学建模 Agent 工作区套件

本仓库不是只面向 Codex 的全局插件，而是一个可以直接作为工作目录使用的 Agent 套件。它同时提供项目级指令、数学建模 Skill 和 Dify 九库检索桥，适用于 Codex、Claude Code，以及其他能够读取项目说明或连接 stdio MCP 的 Agent。

## 目录结构

```text
math-modeling-skill-dify/
├─ AGENTS.md                       # 通用 Agent 工作区指令
├─ CLAUDE.md                       # Claude Code 入口
├─ .agents/skills/math-modeling/   # 完整数学建模 Skill
└─ dify_knowledge_bridge/          # Dify 8 个文本库 + 1 个多模态库检索与治理工具
```

## 最简单的使用方式

1. 下载并解压 Release。
2. 将解压后的文件夹直接作为工作目录打开；也可以把其中内容合并到现有项目根目录。现有项目已经有 `AGENTS.md` 或 `CLAUDE.md` 时，应合并指令而不是直接覆盖。
3. 启动你常用的 Agent。
4. 发送：

```text
请读取本工作区的 AGENTS.md；启用数学建模套件，并引导我配置 Dify 知识库。
```

Claude Code 也会从 `CLAUDE.md` 得到相同入口。

Agent 应自行完成：

- 读取 `.agents/skills/math-modeling/SKILL.md`；
- 检测当前是 Codex、Claude Code 还是其他客户端；
- 询问 Dify Service API 地址、密钥和知识库名称；
- 创建本项目专用 `.venv`；
- 配置九库 YAML；
- 按当前客户端支持的方式注册项目级 stdio MCP；
- 运行单元测试、连接诊断和 MCP 协议测试。

无需 Dify 也能使用本地数学建模 Skill；此时知识库检索会降级为不可用，但建模、编程、Office/PDF 工具和论文工作流仍保留。

## 手工配置入口

需要排查或手工操作时，阅读：

- `dify_knowledge_bridge/README.md`：Dify 地址、密钥与九库 YAML；
- `dify_knowledge_bridge/MCP_SETUP.md`：通用 stdio MCP 参数和客户端适配原则；
- `.agents/skills/math-modeling/SKILL.md`：完整建模工作流。

## 基本测试

```powershell
cd .\dify_knowledge_bridge
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest test_kb_bridge.py
```

配置 `.env` 和 `knowledge_bases.yaml` 后再运行：

```powershell
.\.venv\Scripts\python .\kb_bridge.py doctor
.\.venv\Scripts\python .\test_mcp_protocol.py
```

## 安全与许可

`.env`、虚拟环境、缓存和查询结果均被 Git 忽略。不要把真实 Dify 密钥提交到仓库。

代码与 Skill 说明按 MIT License 发布；其中引用的竞赛规则、论文、图片、模板和第三方工具仍遵循各自许可。
