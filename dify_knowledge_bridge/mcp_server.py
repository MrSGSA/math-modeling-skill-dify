from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent

import kb_bridge


ROOT = Path(__file__).resolve().parent

mcp = FastMCP(
    "数学建模知识库",
    instructions=(
        "统一检索八个数学建模文本知识库和一个多模态案例库。"
        "回答数学建模、竞赛规则、算法、代码、论文写作、领域知识、优秀案例或练习复盘相关问题前，"
        "优先使用 search_math_modeling_knowledge。文字库用于回答主体，多模态库用于补充相关原图。"
    ),
)


def load_runtime() -> tuple[dict[str, Any], dict[str, Any]]:
    kb_bridge.load_dotenv(ROOT / ".env")
    config = kb_bridge.load_config(ROOT / "knowledge_bases.yaml")
    mcp_config = config.get("mcp") or {}
    if not isinstance(mcp_config, dict):
        raise kb_bridge.BridgeError("knowledge_bases.yaml 中的 mcp 必须是对象。")
    return config, mcp_config


def clipped(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def compact_result(result: dict[str, Any], max_chars: int, max_total: int) -> dict[str, Any]:
    used = 0

    def compact_hits(items: list[dict[str, Any]], include_children: bool) -> list[dict[str, Any]]:
        nonlocal used
        output: list[dict[str, Any]] = []
        for item in items:
            remaining = max_total - used
            if remaining <= 0:
                break
            content = clipped(item.get("content"), min(max_chars, remaining))
            used += len(content)
            entry: dict[str, Any] = {
                "database": item.get("database_name"),
                "score": item.get("score"),
                "document": item.get("document_name"),
                "document_id": item.get("document_id"),
                "segment_id": item.get("segment_id"),
                "content": content,
            }
            if include_children:
                entry["matched_child_chunks"] = item.get("matched_child_chunks", [])
            output.append(entry)
        return output

    images = [
        {
            "score": item.get("score"),
            "document": item.get("document_name"),
            "segment_id": item.get("segment_id"),
            "file_id": item.get("file_id"),
            "name": item.get("name"),
            "mime_type": item.get("mime_type"),
            "source_url": item.get("source_url"),
            "context": clipped(item.get("context"), min(max_chars, 1800)),
        }
        for item in result.get("image_hits", [])
    ]
    return {
        "query": result.get("query"),
        "knowledge_scope": result.get("knowledge_scope"),
        "elapsed_ms": result.get("elapsed_ms"),
        "summary": result.get("summary"),
        "databases": result.get("databases", []),
        "errors": result.get("errors", []),
        "warnings": result.get("warnings", []),
        "usage_guidance": {
            "text_hits": "回答主体和论据",
            "multimodal_context_hits": "与原图绑定的案例上下文，可辅助回答",
            "images": "相关原图；按下面 MCP 图片内容块的编号对应",
            "experience_boundary": (
                "经验层命中只用于候选思路、风险提示和验证设计；必须按当前题重新推导和运行，"
                "不得当作当前题答案或独立正确性证据。"
            ),
        },
        "text_hits": compact_hits(result.get("text_hits", []), include_children=True),
        "multimodal_context_hits": compact_hits(
            result.get("multimodal_context_hits", []), include_children=True
        ),
        "images": images,
    }


def download_image(url: str, timeout: float, max_bytes: int) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "image/*", "User-Agent": "math-modeling-knowledge-mcp/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
            mime_type = str(response.headers.get_content_type() or "image/jpeg")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise kb_bridge.BridgeError(f"下载图片失败：{exc}") from exc
    if len(data) > max_bytes:
        raise kb_bridge.BridgeError(f"图片超过 MCP 限制 {max_bytes} 字节。")
    if not mime_type.startswith("image/"):
        raise kb_bridge.BridgeError(f"图片地址返回了非图片类型：{mime_type}")
    return data, mime_type


@mcp.tool()
def search_math_modeling_knowledge(
    query: str,
    include_images: bool = True,
    max_images: int = 4,
    knowledge_scope: str = "core",
) -> CallToolResult:
    """按核心层、经验层或全部范围检索数学建模知识库。

    Args:
        query: 明确、独立的检索问题，最长 250 字。
        include_images: 是否把命中的真实图片作为 MCP 图片内容块返回。
        max_images: 最多返回几张图片，默认 4，配置上限 8。
        knowledge_scope: core=5个核心文本库；experience=3个经验文本库；multimodal=只查多模态库；all=8个文本库。默认 core。
    """
    config, mcp_config = load_runtime()
    normalized_scope = str(knowledge_scope or "core").strip().lower()
    scoped_config = kb_bridge.config_for_scope(config, normalized_scope)
    result = kb_bridge.execute_query(scoped_config, query)
    result["knowledge_scope"] = normalized_scope
    max_chars = max(500, int(mcp_config.get("max_chars_per_hit", 4000)))
    max_total = max(4000, int(mcp_config.get("max_total_text_chars", 48000)))
    configured_default = int(mcp_config.get("default_image_count", 4))
    configured_max = max(0, int(mcp_config.get("max_image_count", 8)))
    if max_images is None:
        max_images = configured_default
    image_limit = min(max(0, int(max_images)), configured_max)
    max_image_bytes = max(65536, int(mcp_config.get("max_image_bytes", 2097152)))
    timeout = float(config.get("dify", {}).get("timeout_seconds", 60))

    compact = compact_result(result, max_chars, max_total)
    downloaded: list[tuple[dict[str, Any], bytes, str]] = []
    image_errors: list[str] = []
    if include_images and image_limit:
        for image in compact["images"][:image_limit]:
            url = str(image.get("source_url") or "")
            if not url:
                image_errors.append(f"{image.get('name')}: 没有 source_url")
                continue
            try:
                data, mime_type = download_image(url, timeout, max_image_bytes)
                downloaded.append((image, data, mime_type))
            except Exception as exc:
                image_errors.append(f"{image.get('name')}: {exc}")
    compact["returned_image_blocks"] = len(downloaded)
    compact["image_download_errors"] = image_errors

    content: list[TextContent | ImageContent] = [
        TextContent(type="text", text=json.dumps(compact, ensure_ascii=False, indent=2))
    ]
    for index, (image, data, mime_type) in enumerate(downloaded, start=1):
        content.append(
            TextContent(
                type="text",
                text=(
                    f"图片 {index}/{len(downloaded)}：{image.get('name')}\n"
                    f"来源文档：{image.get('document')}\n"
                    f"相关度：{image.get('score')}"
                ),
            )
        )
        content.append(
            ImageContent(
                type="image",
                data=base64.b64encode(data).decode("ascii"),
                mimeType=mime_type,
            )
        )
    return CallToolResult(content=content)


@mcp.tool()
def math_modeling_knowledge_status() -> dict[str, Any]:
    """检查 Dify 连接、API 密钥以及 YAML 中所有知识库的名称与 ID 是否匹配。"""
    config, _ = load_runtime()
    base_url, api_key, timeout, _ = kb_bridge.api_settings(config)
    remote = kb_bridge.list_all_datasets(base_url, api_key, timeout)
    resolved, errors = kb_bridge.resolve_databases(config, remote)
    declared = config.get("knowledge_bases", [])
    return {
        "ok": not errors and bool(resolved),
        "declared_count": len(declared),
        "configured_count": sum(bool(item.get("enabled", True)) for item in declared if isinstance(item, dict)),
        "resolved_count": len(resolved),
        "errors": errors,
        "knowledge_bases": [
            {"key": item.key, "name": item.name, "role": item.role, "dataset_id": item.dataset_id}
            for item in resolved
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
