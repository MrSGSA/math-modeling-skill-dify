# 数学建模 Skill + Dify 九库检索桥

这是一套可直接分享给同学使用的 Codex 数学建模工具包，包含：

- `math-modeling/`：建模手、编程手、论文手三阶段 Skill，以及 PDF、DOCX、XLSX、论文搜索和知识库预处理工具；
- `dify-knowledge-bridge/`：统一查询 8 个文本知识库和 1 个多模态知识库的 MCP 服务；
- `install_skill.ps1`：把 Skill 安装到当前用户的 Codex Skill 目录。

## 环境要求

- Windows 10/11；
- Python 3.10 或更高版本；
- 已安装并可运行 `codex` 命令；
- 使用知识库检索时，需要一把 Dify 知识库 Service API 密钥。

Word 后台渲染、MATLAB、Pandoc 和 LibreOffice 只在相应功能需要时安装，不是基础检索的必需依赖。

## 1. 安装数学建模 Skill

在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_skill.ps1
```

脚本默认复制到：

```text
%CODEX_HOME%\skills\math-modeling
```

若未设置 `CODEX_HOME`，则使用：

```text
%USERPROFILE%\.codex\skills\math-modeling
```

如果目标目录已经存在，脚本会停止，避免覆盖本地版本。需要升级时，请先自行备份或移走旧目录。

也可以把 `math-modeling/` 手工复制到具体项目的：

```text
<项目目录>\.agents\skills\math-modeling
```

## 2. 配置 Dify 九库检索桥

```powershell
cd .\dify-knowledge-bridge
Copy-Item .env.example .env
notepad .env
```

在 `.env` 中填写：

```text
DIFY_KNOWLEDGE_API_KEY=你的知识库Service API密钥
```

随后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_or_repair_codex_mcp.ps1
```

安装脚本会创建隔离虚拟环境、安装依赖、注册 `math_modeling_knowledge` MCP，并进行协议及知识库连接测试。完成后完全退出并重新打开 Codex。

如果使用自己的 Dify 实例，请修改 `dify-knowledge-bridge/knowledge_bases.yaml`：

- 将 `dify.base_url` 改为自己的 Dify Service API 地址；
- 将各库的 `dify_name` 改为实际名称；
- `dataset_id` 可填写真实 ID，也可写成 `auto` 按名称自动发现；
- 不需要的知识库可将 `enabled` 改为 `false`。

## 3. 使用方式

安装成功后，可直接向 Codex 提出：

```text
利用数学建模 Skill 完成这道赛题，先检索核心知识库，再依次完成建模、编程和论文阶段。
```

知识库 MCP 提供两个工具：

- `search_math_modeling_knowledge`：按 `core`、`experience`、`all` 或 `multimodal` 范围检索；
- `math_modeling_knowledge_status`：检查 Dify 连接和九库匹配状态。

多模态检索应先从文字库确定来源文档、图号或图表主题，再定向查图。返回的案例只作为可迁移经验，不能替代题目条件、代码运行结果和独立验证。

## 4. 本地测试

无需 Dify 密钥的桥接单元测试：

```powershell
cd .\dify-knowledge-bridge
python -m pip install -r requirements.txt
python -m unittest test_kb_bridge.py
```

配置密钥后可进一步运行：

```powershell
python .\kb_bridge.py doctor
python .\test_mcp_protocol.py
```

## 安全说明

仓库不会包含 `.env`、`.venv`、缓存、最近查询结果或 API 密钥。请通过私下渠道向同学提供可用密钥，或让他们连接自己的 Dify 实例。不要把真实密钥提交到 Git。

## 许可

代码与 Skill 说明按 MIT License 发布。引用的竞赛规则、论文、图片、模板或第三方工具仍分别遵循其原始版权和许可要求。
