from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import urllib.parse
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import kb_bridge


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
OUTPUT_ROOT = PROJECT_ROOT / "knowledge_governance"

FIELD_SCHEMA: tuple[tuple[str, str], ...] = (
    ("document_type", "string"),
    ("authority", "string"),
    ("domain", "string"),
    ("competition", "string"),
    ("year", "number"),
    ("lifecycle", "string"),
    ("retrieval_tier", "string"),
    ("source_group", "string"),
    ("verification_status", "string"),
)

KNOWN_DUPLICATES = {
    "灰色系统理论及其应用.md",
    "经济与金融中的优化问题.md",
}

ARCHIVE_PATTERNS = (
    r"^比赛心得\.md$",
    r"^选题、命题介绍分析\.md$",
    r"^数学建模论文超级模板\.md$",
    r"^参考文献\.md$",
    r"^lindo1\.md$",
    r"LINGO8[_\. ]?0",
    r"^lingo使用指南",
    r"^_LINGO软件的基本使用方法",
    r"Lingo优化软件及其应用--灵敏度分析",
)


def load_runtime() -> tuple[dict[str, Any], str, str, float, list[kb_bridge.Database]]:
    kb_bridge.load_dotenv(ROOT / ".env")
    config = kb_bridge.load_config(ROOT / "knowledge_bases.yaml")
    base_url, api_key, timeout, _ = kb_bridge.api_settings(config)
    remote = kb_bridge.list_all_datasets(base_url, api_key, timeout)
    databases, errors = kb_bridge.resolve_databases(config, remote)
    if errors:
        raise kb_bridge.BridgeError("；".join(errors))
    return config, base_url, api_key, timeout, databases


def list_documents(
    base_url: str, api_key: str, timeout: float, dataset_id: str
) -> list[dict[str, Any]]:
    page = 1
    output: list[dict[str, Any]] = []
    while True:
        query = urllib.parse.urlencode({"page": page, "limit": 100})
        response = kb_bridge.request_json(
            "GET", f"{base_url}/datasets/{dataset_id}/documents?{query}", api_key, timeout
        )
        batch = response.get("data", [])
        if not isinstance(batch, list):
            raise kb_bridge.BridgeError("文档列表的 data 字段不是列表。")
        output.extend(item for item in batch if isinstance(item, dict))
        if not response.get("has_more"):
            return output
        page += 1
        if page > 1000:
            raise kb_bridge.BridgeError("文档分页超过 1000 页，已停止。")


def list_metadata(
    base_url: str, api_key: str, timeout: float, dataset_id: str
) -> dict[str, Any]:
    return kb_bridge.request_json(
        "GET", f"{base_url}/datasets/{dataset_id}/metadata", api_key, timeout
    )


def current_metadata(document: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in document.get("doc_metadata") or []:
        if isinstance(item, dict) and item.get("name"):
            values[str(item["name"])] = item.get("value")
    return values


def first_year(name: str) -> int | None:
    match = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", name)
    return int(match.group(1)) if match else None


def infer_domain(name: str, database_key: str) -> str:
    rules = (
        (r"流体|水动力|湍流|管流|Navier|CFD", "流体力学"),
        (r"传热|热力|温度|炉温|热传导", "传热与热力学"),
        (r"力学|碰撞|振动|刚体|弹性|结构", "工程力学"),
        (r"微分方程|动力系统|稳定状态|传染病|种群", "动力系统"),
        (r"图论|网络优化|最短路|最大流", "图论与网络"),
        (r"灰色|层次分析|模糊|评价|决策|TOPSIS", "评价与决策"),
        (r"神经网络|支持向量|机器学习|聚类|分类", "机器学习"),
        (r"时间序列|回归|统计|预测|概率", "统计与预测"),
        (r"优化|规划|LINGO|LINDO|遗传算法|模拟退火", "优化与运筹"),
        (r"图像|视觉|像素", "图像处理"),
        (r"论文|摘要|写作|参考文献", "论文写作"),
        (r"规则|规范|章程|参赛", "竞赛规则"),
    )
    for pattern, value in rules:
        if re.search(pattern, name, re.IGNORECASE):
            return value
    defaults = {
        "programming": "编程实现",
        "modeling_algorithms": "建模方法",
        "competition_rules": "竞赛规则",
        "domain_knowledge": "跨学科领域知识",
        "paper_writing": "论文写作",
        "excellent_cases": "综合案例",
        "mistake_notebook": "建模防错",
        "practice_reviews": "建模复盘",
        "multimodal_cases": "视觉资料",
    }
    return defaults.get(database_key, "数学建模")


def infer_document_type(name: str, database_key: str) -> str:
    if database_key == "competition_rules":
        return "官方规则"
    if database_key == "excellent_cases":
        return "优秀论文"
    if database_key == "mistake_notebook":
        return "错题条目"
    if database_key == "practice_reviews":
        return "实践复盘"
    if database_key == "multimodal_cases":
        return "多模态资料包"
    if re.search(r"模板", name):
        return "模板"
    if re.search(r"参考文献|书目", name):
        return "书目"
    if re.search(r"报告|命题线索|方法谱系", name):
        return "研究报告"
    if re.search(r"第\d+章|第[一二三四五六七八九十]+章|第\d+课", name):
        return "教材章节"
    if database_key == "programming":
        return "编程教程"
    if database_key == "modeling_algorithms":
        return "算法教材或讲义"
    if database_key == "domain_knowledge":
        return "领域资料"
    if database_key == "paper_writing":
        return "写作资料"
    return "参考资料"


def infer_authority(name: str, database_key: str) -> str:
    if database_key == "competition_rules":
        return "官方"
    if database_key == "excellent_cases":
        return "竞赛优秀论文"
    if database_key in {"mistake_notebook", "practice_reviews"}:
        return "内部验证"
    if database_key == "multimodal_cases":
        return "转换资料"
    if re.search(r"全国大学生|组委会|官方", name):
        return "官方"
    if database_key in {"programming", "modeling_algorithms"}:
        return "教材或讲义"
    if database_key == "paper_writing":
        return "经验资料"
    return "研究资料"


def infer_competition(name: str, database_key: str) -> str:
    if re.search(r"MCM|ICM", name, re.IGNORECASE):
        return "MCM/ICM"
    if re.search(r"研究生|华为杯", name):
        return "中国研究生数学建模竞赛"
    if database_key in {
        "competition_rules",
        "excellent_cases",
        "mistake_notebook",
        "practice_reviews",
    } or re.search(r"CUMCM|国赛|\d{4}[-_ ]?[ABCD]\d*", name, re.IGNORECASE):
        return "CUMCM"
    return "通用"


def source_group(name: str, database_key: str) -> str:
    value = name.strip()
    value = re.sub(r"\.dify-mm\.epub$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\.(md|docx?|pdf|epub|pptx?|xlsx?)$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^第\d+章[_\- ]*", "", value)
    value = re.sub(r"^第[一二三四五六七八九十]+章[_\- ]*", "", value)
    if database_key not in {"excellent_cases", "multimodal_cases"}:
        value = re.sub(r"_?\(\d+\)$", "", value)
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[（(]z-library.*$", "", value, flags=re.IGNORECASE)
    return value[:255] or "未命名来源"


def lifecycle_for(name: str, database_key: str) -> str:
    if name in KNOWN_DUPLICATES:
        return "duplicate"
    if re.search(r"2026A题命题线索|2026A题备赛", name):
        return "temporary"
    if any(re.search(pattern, name, re.IGNORECASE) for pattern in ARCHIVE_PATTERNS):
        return "archive"
    return "active"


def proposed_metadata(
    document: dict[str, Any], database_key: str
) -> tuple[dict[str, Any], dict[str, str]]:
    name = str(document.get("name") or "")
    lifecycle = lifecycle_for(name, database_key)
    tier = "archive" if lifecycle in {"archive", "duplicate"} else "supplement"
    if lifecycle == "active" and database_key in {
        "modeling_algorithms",
        "competition_rules",
        "domain_knowledge",
        "mistake_notebook",
        "practice_reviews",
    }:
        tier = "primary"
    if database_key == "competition_rules":
        verification = "官方已核验"
    elif database_key in {"mistake_notebook", "practice_reviews"}:
        verification = "已验证"
    elif database_key == "excellent_cases":
        verification = "外部案例"
    elif lifecycle in {"archive", "duplicate"}:
        verification = "历史资料"
    else:
        verification = "待核验"
    inferred: dict[str, Any] = {
        "document_type": infer_document_type(name, database_key),
        "authority": infer_authority(name, database_key),
        "domain": infer_domain(name, database_key),
        "competition": infer_competition(name, database_key),
        "year": first_year(name),
        "lifecycle": lifecycle,
        "retrieval_tier": tier,
        "source_group": source_group(name, database_key),
        "verification_status": verification,
    }
    existing = current_metadata(document)
    provenance: dict[str, str] = {}
    merged: dict[str, Any] = {}
    for field, _ in FIELD_SCHEMA:
        if field in existing and existing[field] not in (None, ""):
            merged[field] = existing[field]
            provenance[field] = "existing"
        else:
            merged[field] = inferred[field]
            provenance[field] = "inferred"
    return merged, provenance


def create_fields(
    base_url: str,
    api_key: str,
    timeout: float,
    dataset_id: str,
    metadata_response: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    current = {
        str(item.get("name")): dict(item)
        for item in metadata_response.get("doc_metadata") or []
        if isinstance(item, dict) and item.get("name")
    }
    for name, field_type in FIELD_SCHEMA:
        if name in current:
            if str(current[name].get("type")) != field_type:
                raise kb_bridge.BridgeError(
                    f"字段 {name} 类型为 {current[name].get('type')}，期望 {field_type}。"
                )
            continue
        created = kb_bridge.request_json(
            "POST",
            f"{base_url}/datasets/{dataset_id}/metadata",
            api_key,
            timeout,
            {"type": field_type, "name": name},
        )
        current[name] = created
    return current


def write_batch(
    base_url: str,
    api_key: str,
    timeout: float,
    dataset_id: str,
    fields: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
) -> None:
    operations = []
    for row in rows:
        values = row["proposed_metadata"]
        metadata_list = [
            {"id": fields[name]["id"], "name": name, "value": values.get(name)}
            for name, _ in FIELD_SCHEMA
        ]
        operations.append(
            {
                "document_id": row["document_id"],
                "metadata_list": metadata_list,
                "partial_update": True,
            }
        )
    response = kb_bridge.request_json(
        "POST",
        f"{base_url}/datasets/{dataset_id}/documents/metadata",
        api_key,
        timeout,
        {"operation_data": operations},
    )
    if response.get("result") != "success":
        raise kb_bridge.BridgeError(f"批量更新返回异常：{response}")


def write_outputs(run_dir: Path, snapshot: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "inventory_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "metadata_preview.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    columns = [
        "database_name",
        "database_key",
        "document_id",
        "document_name",
        "enabled",
        "archived",
        *(name for name, _ in FIELD_SCHEMA),
    ]
    with (run_dir / "metadata_preview.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            flat = {key: row.get(key) for key in columns[:7]}
            flat.update(row["proposed_metadata"])
            writer.writerow(flat)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "documents": len(rows),
        "by_database": dict(Counter(row["database_name"] for row in rows)),
        "by_lifecycle": dict(
            Counter(row["proposed_metadata"]["lifecycle"] for row in rows)
        ),
        "by_retrieval_tier": dict(
            Counter(row["proposed_metadata"]["retrieval_tier"] for row in rows)
        ),
        "preserved_existing_values": sum(
            1
            for row in rows
            for value in row["provenance"].values()
            if value == "existing"
        ),
    }


def collect(
    base_url: str,
    api_key: str,
    timeout: float,
    databases: list[kb_bridge.Database],
    selected_key: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    snapshot: dict[str, Any] = {"created_at": datetime.now().isoformat(), "databases": []}
    rows: list[dict[str, Any]] = []
    for database in databases:
        if selected_key and database.key != selected_key:
            continue
        metadata = list_metadata(base_url, api_key, timeout, database.dataset_id)
        documents = list_documents(base_url, api_key, timeout, database.dataset_id)
        snapshot["databases"].append(
            {
                "key": database.key,
                "name": database.name,
                "dataset_id": database.dataset_id,
                "metadata": metadata,
                "documents": documents,
            }
        )
        for document in documents:
            proposed, provenance = proposed_metadata(document, database.key)
            rows.append(
                {
                    "database_name": database.name,
                    "database_key": database.key,
                    "dataset_id": database.dataset_id,
                    "document_id": str(document.get("id") or ""),
                    "document_name": str(document.get("name") or ""),
                    "enabled": document.get("enabled"),
                    "archived": document.get("archived"),
                    "existing_metadata": current_metadata(document),
                    "proposed_metadata": proposed,
                    "provenance": provenance,
                }
            )
    return snapshot, rows


def list_segment_content(
    base_url: str,
    api_key: str,
    timeout: float,
    dataset_id: str,
    document_id: str,
) -> str:
    page = 1
    chunks: list[tuple[int, str]] = []
    while True:
        query = urllib.parse.urlencode({"page": page, "limit": 100})
        response = kb_bridge.request_json(
            "GET",
            f"{base_url}/datasets/{dataset_id}/documents/{document_id}/segments?{query}",
            api_key,
            timeout,
        )
        for item in response.get("data") or []:
            if isinstance(item, dict):
                content = str(item.get("content") or "")
                for child in item.get("child_chunks") or []:
                    if isinstance(child, dict):
                        content += "\n" + str(child.get("content") or "")
                chunks.append((int(item.get("position") or 0), content))
        if not response.get("has_more"):
            break
        page += 1
        if page > 1000:
            raise kb_bridge.BridgeError("分段分页超过 1000 页，已停止。")
    return "\n".join(content for _, content in sorted(chunks))


def normalized_content(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def shingles(value: str, width: int = 17, step: int = 7) -> set[str]:
    if len(value) <= width:
        return {value} if value else set()
    return {value[index : index + width] for index in range(0, len(value) - width + 1, step)}


def content_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    left_set = shingles(left)
    right_set = shingles(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def apply_duplicate_fingerprints(
    base_url: str,
    api_key: str,
    timeout: float,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["database_key"], row["proposed_metadata"]["source_group"])
        groups.setdefault(key, []).append(row)
    audit: list[dict[str, Any]] = []
    candidate_groups = {
        key: candidates
        for key, candidates in groups.items()
        if len(candidates) >= 2
        and key[0]
        not in {
            "excellent_cases",
            "mistake_notebook",
            "practice_reviews",
            "multimodal_cases",
        }
    }
    candidate_rows = {
        row["document_id"]: row
        for candidates in candidate_groups.values()
        for row in candidates
    }
    content_cache: dict[str, str] = {}

    def fetch(row: dict[str, Any]) -> tuple[str, str]:
        text = list_segment_content(
            base_url,
            api_key,
            timeout,
            row["dataset_id"],
            row["document_id"],
        )
        return row["document_id"], normalized_content(text)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch, row): row for row in candidate_rows.values()}
        for future in as_completed(futures):
            document_id, normalized = future.result()
            content_cache[document_id] = normalized
            row = candidate_rows[document_id]
            row["content_sha256"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            row["normalized_content_chars"] = len(normalized)

    for (database_key, group), candidates in candidate_groups.items():
        # Prefer a numbered chapter as the canonical copy; otherwise keep the first positioned document.
        canonical = sorted(
            candidates,
            key=lambda row: (
                0 if re.search(r"^第\d+章", row["document_name"]) else 1,
                row["document_name"],
            ),
        )[0]
        for row in candidates:
            if row is canonical:
                continue
            similarity = content_similarity(
                content_cache[canonical["document_id"]], content_cache[row["document_id"]]
            )
            record = {
                "database": row["database_name"],
                "source_group": group,
                "canonical": canonical["document_name"],
                "candidate": row["document_name"],
                "similarity": round(similarity, 6),
                "marked_duplicate": similarity >= 0.97,
            }
            audit.append(record)
            if similarity >= 0.97:
                row["proposed_metadata"]["lifecycle"] = "duplicate"
                row["proposed_metadata"]["retrieval_tier"] = "archive"
                row["proposed_metadata"]["verification_status"] = "正文指纹重复"
    return audit


def apply_all(
    base_url: str,
    api_key: str,
    timeout: float,
    snapshot: dict[str, Any],
    rows: list[dict[str, Any]],
    batch_size: int,
) -> dict[str, Any]:
    report: dict[str, Any] = {"datasets": [], "documents_updated": 0}
    by_dataset: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_dataset.setdefault(row["dataset_id"], []).append(row)
    snapshots = {item["dataset_id"]: item for item in snapshot["databases"]}
    for dataset_id, dataset_rows in by_dataset.items():
        item = snapshots[dataset_id]
        fields = create_fields(
            base_url, api_key, timeout, dataset_id, item["metadata"]
        )
        # Canary: update one document, then re-read the list and verify all target fields.
        write_batch(base_url, api_key, timeout, dataset_id, fields, dataset_rows[:1])
        time.sleep(0.2)
        refreshed = list_documents(base_url, api_key, timeout, dataset_id)
        refreshed_by_id = {str(doc.get("id")): doc for doc in refreshed}
        canary = current_metadata(refreshed_by_id[dataset_rows[0]["document_id"]])
        missing = [name for name, _ in FIELD_SCHEMA if name not in canary]
        if missing:
            raise kb_bridge.BridgeError(
                f"{item['name']} 小批量回读缺少字段：{', '.join(missing)}"
            )
        updated = 1
        for start in range(1, len(dataset_rows), batch_size):
            batch = dataset_rows[start : start + batch_size]
            write_batch(base_url, api_key, timeout, dataset_id, fields, batch)
            updated += len(batch)
            time.sleep(0.1)
        report["datasets"].append(
            {"name": item["name"], "dataset_id": dataset_id, "updated": updated}
        )
        report["documents_updated"] += updated
    return report


def verify(
    base_url: str,
    api_key: str,
    timeout: float,
    databases: list[kb_bridge.Database],
    selected_key: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"datasets": [], "documents": 0, "complete": 0, "incomplete": []}
    for database in databases:
        if selected_key and database.key != selected_key:
            continue
        documents = list_documents(base_url, api_key, timeout, database.dataset_id)
        complete = 0
        for document in documents:
            values = current_metadata(document)
            missing = [name for name, _ in FIELD_SCHEMA if name not in values]
            if missing:
                result["incomplete"].append(
                    {
                        "database": database.name,
                        "document": document.get("name"),
                        "missing": missing,
                    }
                )
            else:
                complete += 1
        result["datasets"].append(
            {"name": database.name, "documents": len(documents), "complete": complete}
        )
        result["documents"] += len(documents)
        result["complete"] += complete
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Dify 数学建模知识库元数据治理")
    parser.add_argument("--apply", action="store_true", help="创建字段并批量写入元数据")
    parser.add_argument("--dataset", help="仅处理 knowledge_bases.yaml 中的指定 key")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-fingerprint", action="store_true", help="不读取分段正文检查重复")
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 100:
        parser.error("--batch-size 必须在 1 到 100 之间")

    _, base_url, api_key, timeout, databases = load_runtime()
    known_keys = {database.key for database in databases}
    if args.dataset and args.dataset not in known_keys:
        parser.error(f"未知 dataset key：{args.dataset}")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.output or OUTPUT_ROOT / stamp
    snapshot, rows = collect(base_url, api_key, timeout, databases, args.dataset)
    duplicate_audit: list[dict[str, Any]] = []
    if not args.no_fingerprint:
        duplicate_audit = apply_duplicate_fingerprints(
            base_url, api_key, timeout, rows
        )
    write_outputs(run_dir, snapshot, rows)
    summary = summarize(rows)
    summary["duplicate_fingerprint_comparisons"] = len(duplicate_audit)
    summary["fingerprint_duplicates"] = sum(
        1 for item in duplicate_audit if item["marked_duplicate"]
    )
    (run_dir / "preview_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "duplicate_fingerprint_audit.json").write_text(
        json.dumps(duplicate_audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output: dict[str, Any] = {"run_dir": str(run_dir), "preview": summary}
    if args.apply:
        apply_report = apply_all(
            base_url, api_key, timeout, snapshot, rows, args.batch_size
        )
        verification = verify(base_url, api_key, timeout, databases, args.dataset)
        (run_dir / "apply_report.json").write_text(
            json.dumps(apply_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "verification_report.json").write_text(
            json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        output["apply"] = apply_report
        output["verification"] = verification
        if verification["incomplete"]:
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except kb_bridge.BridgeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
