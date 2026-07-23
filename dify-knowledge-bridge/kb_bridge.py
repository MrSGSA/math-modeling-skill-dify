from __future__ import annotations

import argparse
import copy
import concurrent.futures
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "knowledge_bases.yaml"
DEFAULT_ENV = ROOT / ".env"
DEFAULT_RESULT = ROOT / "last_result.json"

KNOWLEDGE_SCOPE_KEYS = {
    "core": {
        "programming",
        "modeling_algorithms",
        "competition_rules",
        "domain_knowledge",
        "paper_writing",
    },
    "experience": {
        "excellent_cases",
        "mistake_notebook",
        "practice_reviews",
    },
    "multimodal": {
        "multimodal_cases",
    },
}


class BridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Database:
    key: str
    name: str
    dataset_id: str
    role: str
    description: str
    retrieval: dict[str, Any]


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise BridgeError(f"配置文件不存在：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise BridgeError("YAML 顶层必须是对象。")
    if not isinstance(data.get("dify"), dict):
        raise BridgeError("YAML 缺少 dify 配置。")
    if not isinstance(data.get("knowledge_bases"), list):
        raise BridgeError("YAML 缺少 knowledge_bases 列表。")
    return data


def config_for_scope(config: dict[str, Any], scope: str) -> dict[str, Any]:
    """Return a copied config limited to core, experience, or all databases."""
    normalized = str(scope or "core").strip().lower()
    if normalized == "all":
        keys = KNOWLEDGE_SCOPE_KEYS["core"] | KNOWLEDGE_SCOPE_KEYS["experience"]
    else:
        keys = KNOWLEDGE_SCOPE_KEYS.get(normalized)
    if keys is None:
        raise BridgeError("knowledge_scope 必须是 core、experience、multimodal 或 all。")
    scoped = copy.deepcopy(config)
    for item in scoped.get("knowledge_bases", []):
        if not isinstance(item, dict):
            continue
        item["enabled"] = bool(item.get("enabled", True)) and str(item.get("key", "")) in keys
    return scoped


def api_settings(config: dict[str, Any]) -> tuple[str, str, float, int]:
    dify = config["dify"]
    base_url = str(dify.get("base_url", "")).strip().rstrip("/")
    key_env = str(dify.get("api_key_env", "DIFY_KNOWLEDGE_API_KEY")).strip()
    api_key = os.environ.get(key_env, "").strip()
    timeout = float(dify.get("timeout_seconds", 60))
    workers = max(1, int(dify.get("max_workers", 7)))
    if not base_url.startswith(("http://", "https://")):
        raise BridgeError(f"dify.base_url 无效：{base_url!r}")
    if not api_key:
        raise BridgeError(f"尚未设置 {key_env}。请编辑 {DEFAULT_ENV}，把密钥填到等号后。")
    return base_url, api_key, timeout, workers


def request_json(
    method: str,
    url: str,
    api_key: str,
    timeout: float,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "math-modeling-dify-bridge/1.0",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise BridgeError(f"HTTP {exc.code}：{detail[:800]}") from exc
    except urllib.error.URLError as exc:
        raise BridgeError(f"无法连接 Dify：{exc.reason}") from exc
    except TimeoutError as exc:
        raise BridgeError(f"请求 Dify 超时（{timeout:g} 秒）") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BridgeError(f"Dify 返回的不是 JSON：{raw[:500]}") from exc
    if not isinstance(result, dict):
        raise BridgeError("Dify 返回了非对象 JSON。")
    return result


def list_all_datasets(base_url: str, api_key: str, timeout: float) -> list[dict[str, Any]]:
    page = 1
    datasets: list[dict[str, Any]] = []
    while True:
        query = urllib.parse.urlencode({"page": page, "limit": 100, "include_all": "true"})
        response = request_json("GET", f"{base_url}/datasets?{query}", api_key, timeout)
        batch = response.get("data", [])
        if not isinstance(batch, list):
            raise BridgeError("GET /datasets 的 data 字段不是列表。")
        datasets.extend(item for item in batch if isinstance(item, dict))
        if not response.get("has_more"):
            break
        page += 1
        if page > 100:
            raise BridgeError("知识库分页超过 100 页，已停止。")
    return datasets


def normalized_name(value: Any) -> str:
    return "".join(str(value or "").split()).casefold()


def resolve_databases(config: dict[str, Any], remote: list[dict[str, Any]]) -> tuple[list[Database], list[str]]:
    defaults = config.get("defaults", {}).get("retrieval", {})
    if not isinstance(defaults, dict):
        raise BridgeError("defaults.retrieval 必须是对象。")

    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in remote:
        by_name.setdefault(normalized_name(item.get("name")), []).append(item)

    resolved: list[Database] = []
    errors: list[str] = []
    seen_keys: set[str] = set()
    for raw in config["knowledge_bases"]:
        if not isinstance(raw, dict) or not raw.get("enabled", True):
            continue
        key = str(raw.get("key", "")).strip()
        name = str(raw.get("dify_name", "")).strip()
        role = str(raw.get("role", "text")).strip().lower()
        if not key or not name:
            errors.append("存在缺少 key 或 dify_name 的启用项。")
            continue
        if key in seen_keys:
            errors.append(f"配置 key 重复：{key}")
            continue
        seen_keys.add(key)
        if role not in {"text", "multimodal"}:
            errors.append(f"{name} 的 role 必须是 text 或 multimodal。")
            continue

        configured_id = str(raw.get("dataset_id", "auto") or "auto").strip()
        if configured_id.lower() == "auto":
            matches = by_name.get(normalized_name(name), [])
            if not matches:
                errors.append(f"未找到知识库：{name}")
                continue
            if len(matches) > 1:
                ids = ", ".join(str(item.get("id")) for item in matches)
                errors.append(f"知识库名称重复：{name}（{ids}），请在 YAML 明确填写 dataset_id。")
                continue
            dataset_id = str(matches[0].get("id", "")).strip()
        else:
            dataset_id = configured_id

        retrieval = dict(defaults)
        override = raw.get("retrieval") or {}
        if not isinstance(override, dict):
            errors.append(f"{name} 的 retrieval 必须是对象。")
            continue
        retrieval.update(override)
        retrieval.setdefault("search_method", "hybrid_search")
        retrieval.setdefault("top_k", 3)
        retrieval.setdefault("reranking_enable", False)
        retrieval.setdefault("score_threshold_enabled", False)
        retrieval.setdefault("score_threshold", 0.0)
        resolved.append(
            Database(
                key=key,
                name=name,
                dataset_id=dataset_id,
                role=role,
                description=str(raw.get("description", "")).strip(),
                retrieval=retrieval,
            )
        )
    return resolved, errors


def absolute_file_url(base_url: str, source_url: Any) -> str:
    value = str(source_url or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    parsed = urllib.parse.urlsplit(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}/"
    return urllib.parse.urljoin(origin, value.lstrip("/"))


def clean_text(value: Any) -> str:
    return "\n".join(line.rstrip() for line in str(value or "").strip().splitlines()).strip()


def query_database(
    database: Database,
    query: str,
    base_url: str,
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    payload = {"query": query, "retrieval_model": database.retrieval}
    started = time.perf_counter()
    response = request_json(
        "POST",
        f"{base_url}/datasets/{urllib.parse.quote(database.dataset_id)}/retrieve",
        api_key,
        timeout,
        payload,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    records = response.get("records", [])
    if not isinstance(records, list):
        raise BridgeError("检索响应中的 records 不是列表。")
    normalized_records = [dict(record) for record in records if isinstance(record, dict)]
    warnings: list[str] = []

    # Dify 1.14.2 的 /retrieve 只有在“图片向量本身”命中时才填充 files。
    # 文字查询通常命中父块/子块，此时图片已绑定但 files=[]；从 segment 详情补取 attachments。
    if database.role == "multimodal":
        pending: list[tuple[int, str, str]] = []
        for index, record in enumerate(normalized_records):
            if record.get("files"):
                continue
            segment = record.get("segment")
            if not isinstance(segment, dict):
                continue
            document_id = str(segment.get("document_id") or "").strip()
            segment_id = str(segment.get("id") or "").strip()
            if document_id and segment_id:
                pending.append((index, document_id, segment_id))

        def fetch_attachments(document_id: str, segment_id: str) -> list[dict[str, Any]]:
            detail = request_json(
                "GET",
                (
                    f"{base_url}/datasets/{urllib.parse.quote(database.dataset_id)}"
                    f"/documents/{urllib.parse.quote(document_id)}"
                    f"/segments/{urllib.parse.quote(segment_id)}"
                ),
                api_key,
                timeout,
            )
            data = detail.get("data")
            if not isinstance(data, dict):
                return []
            attachments = data.get("attachments") or []
            if not isinstance(attachments, list):
                return []
            return [item for item in attachments if isinstance(item, dict)]

        if pending:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(pending))) as executor:
                future_map = {
                    executor.submit(fetch_attachments, document_id, segment_id): (index, segment_id)
                    for index, document_id, segment_id in pending
                }
                for future in concurrent.futures.as_completed(future_map):
                    index, segment_id = future_map[future]
                    try:
                        normalized_records[index]["files"] = future.result()
                    except Exception as exc:
                        warnings.append(f"父块 {segment_id} 的图片附件补取失败：{exc}")

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return {
        "database": database,
        "records": normalized_records,
        "elapsed_ms": elapsed_ms,
        "warnings": warnings,
    }


def record_parts(record: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    segment = record.get("segment")
    parent = ""
    if isinstance(segment, dict):
        parent = clean_text(segment.get("content"))
    if not parent:
        parent = clean_text(record.get("content"))

    children: list[dict[str, Any]] = []
    raw_children = record.get("child_chunks") or []
    if isinstance(raw_children, list):
        for child in raw_children:
            if not isinstance(child, dict):
                continue
            content = clean_text(child.get("content"))
            if not content:
                continue
            children.append(
                {
                    "id": str(child.get("id") or ""),
                    "position": child.get("position"),
                    "score": score_value(child.get("score")),
                    "content": content,
                }
            )
    return parent, children


def combined_record_content(parent: str, children: list[dict[str, Any]]) -> str:
    unique_children: list[str] = []
    seen = {parent} if parent else set()
    for child in children:
        content = child["content"]
        if content not in seen:
            seen.add(content)
            unique_children.append(content)
    parts = [parent] if parent else []
    if unique_children:
        parts.append("[命中子块]\n" + "\n\n".join(unique_children))
    return "\n\n".join(parts)


def record_document(record: dict[str, Any]) -> tuple[str, str, str]:
    segment = record.get("segment")
    if not isinstance(segment, dict):
        return "", "", ""
    document = segment.get("document")
    if not isinstance(document, dict):
        return str(segment.get("document_id") or ""), "", str(segment.get("id") or "")
    return (
        str(document.get("id") or segment.get("document_id") or ""),
        str(document.get("name") or ""),
        str(segment.get("id") or ""),
    )


def score_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_result(
    query: str,
    outcomes: list[dict[str, Any]],
    failures: list[dict[str, str]],
    config: dict[str, Any],
    base_url: str,
    total_elapsed_ms: int,
) -> dict[str, Any]:
    text_hits: list[dict[str, Any]] = []
    multimodal_context_hits: list[dict[str, Any]] = []
    image_hits: list[dict[str, Any]] = []
    database_stats: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []

    for outcome in outcomes:
        db: Database = outcome["database"]
        records = outcome["records"]
        for warning in outcome.get("warnings", []):
            warnings.append({"database_name": db.name, "warning": str(warning)})
        database_stats.append(
            {
                "key": db.key,
                "name": db.name,
                "role": db.role,
                "dataset_id": db.dataset_id,
                "records": len(records),
                "elapsed_ms": outcome["elapsed_ms"],
            }
        )
        for rank, raw_record in enumerate(records, start=1):
            if not isinstance(raw_record, dict):
                continue
            document_id, document_name, segment_id = record_document(raw_record)
            score = score_value(raw_record.get("score"))
            parent_content, matched_child_chunks = record_parts(raw_record)
            content = combined_record_content(parent_content, matched_child_chunks)
            hit = {
                "database_key": db.key,
                "database_name": db.name,
                "dataset_id": db.dataset_id,
                "rank_in_database": rank,
                "score": score,
                "document_id": document_id,
                "document_name": document_name,
                "segment_id": segment_id,
                "parent_content": parent_content,
                "matched_child_chunks": matched_child_chunks,
                "content": content,
            }
            if db.role == "text":
                if content:
                    text_hits.append(hit)
            else:
                if content:
                    multimodal_context_hits.append(hit)
                files = raw_record.get("files") or []
                if not isinstance(files, list):
                    files = []
                for file_rank, file_info in enumerate(files, start=1):
                    if not isinstance(file_info, dict):
                        continue
                    image_hits.append(
                        {
                            "database_key": db.key,
                            "database_name": db.name,
                            "dataset_id": db.dataset_id,
                            "score": score,
                            "document_id": document_id,
                            "document_name": document_name,
                            "segment_id": segment_id,
                            "context": content,
                            "file_rank": file_rank,
                            "file_id": str(file_info.get("id") or ""),
                            "name": str(file_info.get("name") or ""),
                            "mime_type": str(file_info.get("mime_type") or ""),
                            "size": file_info.get("size"),
                            "source_url": absolute_file_url(base_url, file_info.get("source_url")),
                        }
                    )

    text_hits.sort(key=lambda item: item["score"], reverse=True)
    multimodal_context_hits.sort(key=lambda item: item["score"], reverse=True)
    image_hits.sort(key=lambda item: item["score"], reverse=True)

    merge = config.get("merge", {}) if isinstance(config.get("merge"), dict) else {}
    if merge.get("deduplicate_text", True):
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in text_hits:
            digest = hashlib.sha256(hit["content"].encode("utf-8")).hexdigest()
            if digest not in seen:
                seen.add(digest)
                unique.append(hit)
        text_hits = unique

    if merge.get("deduplicate_images", True):
        unique_images: list[dict[str, Any]] = []
        seen_images: set[str] = set()
        for hit in image_hits:
            identity = hit["file_id"] or hit["source_url"] or f"{hit['segment_id']}:{hit['name']}"
            if identity not in seen_images:
                seen_images.add(identity)
                unique_images.append(hit)
        image_hits = unique_images

    text_hits = text_hits[: max(0, int(merge.get("max_text_hits", 18)))]
    multimodal_context_hits = multimodal_context_hits[
        : max(0, int(merge.get("max_multimodal_context_hits", 6)))
    ]
    image_hits = image_hits[: max(0, int(merge.get("max_image_hits", 12)))]

    return {
        "query": query,
        "elapsed_ms": total_elapsed_ms,
        "summary": {
            "databases_succeeded": len(outcomes),
            "databases_failed": len(failures),
            "text_hits": len(text_hits),
            "multimodal_context_hits": len(multimodal_context_hits),
            "image_hits": len(image_hits),
        },
        "databases": sorted(database_stats, key=lambda item: item["name"]),
        "errors": failures,
        "warnings": warnings,
        "text_hits": text_hits,
        "multimodal_context_hits": multimodal_context_hits,
        "image_hits": image_hits,
    }


def execute_query(config: dict[str, Any], query: str) -> dict[str, Any]:
    """Resolve all configured datasets, query them concurrently, and merge the result."""
    query = str(query or "").strip()
    if not query:
        raise BridgeError("查询不能为空。")
    if len(query) > 250:
        raise BridgeError("Dify 1.14.2 的检索 query 最长为 250 个字符。")

    base_url, api_key, timeout, workers = api_settings(config)
    remote = list_all_datasets(base_url, api_key, timeout)
    databases, resolution_errors = resolve_databases(config, remote)
    if resolution_errors:
        raise BridgeError("知识库配置未全部匹配：" + "；".join(resolution_errors))
    if not databases:
        raise BridgeError("YAML 中没有启用且可用的知识库。")

    started = time.perf_counter()
    outcomes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(databases))) as executor:
        future_map = {
            executor.submit(query_database, db, query, base_url, api_key, timeout): db for db in databases
        }
        for future in concurrent.futures.as_completed(future_map):
            db = future_map[future]
            try:
                outcomes.append(future.result())
            except Exception as exc:  # 单库失败不应阻塞其他库
                failures.append(
                    {
                        "database_key": db.key,
                        "database_name": db.name,
                        "dataset_id": db.dataset_id,
                        "error": str(exc),
                    }
                )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return build_result(query, outcomes, failures, config, base_url, elapsed_ms)


def print_doctor(remote: list[dict[str, Any]], resolved: list[Database], errors: list[str]) -> None:
    print(f"Dify 可见知识库：{len(remote)} 个")
    print(f"YAML 已匹配：{len(resolved)} 个")
    print()
    resolved_by_name = {db.name: db for db in resolved}
    for db in resolved:
        print(f"  [OK] {db.name}  role={db.role}  id={db.dataset_id}")
    for error in errors:
        print(f"  [错误] {error}")
    configured_names = {db.name for db in resolved}
    extras = [item for item in remote if str(item.get("name") or "") not in configured_names]
    if extras:
        print("\nDify 中未纳入本 YAML 的知识库：")
        for item in extras:
            print(f"  - {item.get('name')}  id={item.get('id')}")
    if errors:
        raise BridgeError("配置未全部匹配，请按上面的名称修正 knowledge_bases.yaml。")
    if not resolved_by_name:
        raise BridgeError("没有启用任何知识库。")
    print(f"\n连接、密钥和 {len(resolved)} 个已启用知识库匹配均正常。")


def truncate(value: str, limit: int = 260) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def print_result(result: dict[str, Any], output_path: Path) -> None:
    summary = result["summary"]
    print(f"\n查询：{result['query']}")
    print(
        f"耗时 {result['elapsed_ms'] / 1000:.2f}s；"
        f"成功 {summary['databases_succeeded']} 库，失败 {summary['databases_failed']} 库；"
        f"文本 {summary['text_hits']} 条，图片 {summary['image_hits']} 张。"
    )
    print("\n各库状态：")
    for item in result["databases"]:
        print(f"  [OK] {item['name']}: {item['records']} 条，{item['elapsed_ms']} ms")
    for item in result["errors"]:
        print(f"  [失败] {item['database_name']}: {item['error']}")
    for item in result.get("warnings", []):
        print(f"  [警告] {item['database_name']}: {item['warning']}")

    print("\n文本命中（统一按分数排序）：")
    if not result["text_hits"]:
        print("  （无）")
    for index, hit in enumerate(result["text_hits"], start=1):
        source = hit["document_name"] or hit["document_id"] or "未知文档"
        print(f"  {index}. [{hit['database_name']}] score={hit['score']:.4f} | {source}")
        print(f"     {truncate(hit['content'])}")

    print("\n多模态图片命中：")
    if not result["image_hits"]:
        print("  （本次多模态命中没有返回图片附件；可换一个明确涉及图表的问题再试。）")
    for index, hit in enumerate(result["image_hits"], start=1):
        print(
            f"  {index}. score={hit['score']:.4f} | "
            f"{hit['document_name'] or '未知文档'} | {hit['name'] or hit['file_id']}"
        )
        print(f"     {hit['source_url'] or '未返回 source_url'}")
        if hit["context"]:
            print(f"     上下文：{truncate(hit['context'], 180)}")

    print(f"\n完整机器可读结果：{output_path}")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="统一发现并检索多个 Dify 知识库")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="YAML 配置路径")
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV, help=".env 路径")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="检查 API、密钥和知识库名称匹配")
    query_parser = subparsers.add_parser("query", help="并行检索全部启用的知识库")
    query_parser.add_argument("query", nargs="?", help="检索问题；省略时交互输入")
    query_parser.add_argument("--json", action="store_true", help="只向标准输出打印 JSON")
    query_parser.add_argument("--output", type=Path, default=DEFAULT_RESULT, help="结果 JSON 路径")
    return parser


def main() -> int:
    configure_console()
    args = make_parser().parse_args()
    load_dotenv(args.env.resolve())
    config = load_config(args.config.resolve())
    if args.command == "doctor":
        base_url, api_key, timeout, _ = api_settings(config)
        remote = list_all_datasets(base_url, api_key, timeout)
        databases, resolution_errors = resolve_databases(config, remote)
        print_doctor(remote, databases, resolution_errors)
        return 0

    query = str(args.query or "").strip()
    if not query:
        query = input("请输入要同时检索 7 个知识库的问题：").strip()
    result = execute_query(config, query)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_result(result, args.output.resolve())
    return 0 if result["summary"]["databases_succeeded"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BridgeError as exc:
        print(f"\n错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
