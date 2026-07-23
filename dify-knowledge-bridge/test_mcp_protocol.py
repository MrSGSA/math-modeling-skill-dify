from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parent


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "mcp_server.py")],
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            expected = {"search_math_modeling_knowledge", "math_modeling_knowledge_status"}
            missing = expected - names
            if missing:
                raise RuntimeError(f"MCP 缺少工具：{sorted(missing)}")

            status = await session.call_tool("math_modeling_knowledge_status", {})
            if status.isError:
                raise RuntimeError(f"状态工具失败：{status.content}")

            result = await session.call_tool(
                "search_math_modeling_knowledge",
                {
                    "query": "回焊炉炉温曲线优化模型与相关曲线图",
                    "include_images": True,
                    "max_images": 2,
                    "knowledge_scope": "multimodal",
                },
            )
            if result.isError:
                raise RuntimeError(f"检索工具失败：{result.content}")
            kinds = [getattr(item, "type", "unknown") for item in result.content]
            if "text" not in kinds:
                raise RuntimeError("MCP 检索结果缺少文字内容块。")
            if "image" not in kinds:
                raise RuntimeError("MCP 检索结果缺少图片内容块。")
            print(f"MCP 协议测试通过：tools={sorted(names)}，content={kinds}")


if __name__ == "__main__":
    asyncio.run(main())
