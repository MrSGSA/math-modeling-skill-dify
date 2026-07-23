# Claude 工作区入口

完整读取并遵循同目录的 `AGENTS.md`。

本工作区的数学建模 Skill 位于 `.agents/skills/math-modeling/SKILL.md`。即使当前客户端不会自动发现 `.agents/skills`，也必须在相关任务触发时主动读取该文件，并按其中的渐进式加载规则使用角色、工具和参考资料。

当用户要求启用或配置 Dify 时，按 `AGENTS.md` 自行识别 Claude Code 当前版本支持的项目级 MCP 配置方式；先检查 `claude mcp --help`，不要把 Codex 命令照搬为 Claude 命令。
