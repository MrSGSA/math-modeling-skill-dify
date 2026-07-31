#!/usr/bin/env python3
"""由发布清单驱动的数学建模论文失败即停止门禁。

门禁不从竞赛名称猜测页数、子问题数量或图表数量。候选 DOCX、渲染 PDF、
结果注册表、红队审计、模板和渲染器都必须在发布清单中显式声明并锁定。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
import uuid
from pathlib import Path

import fitz
from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper_format as pf

SKILL_ROOT = Path(__file__).resolve().parents[3]
REVIEWER_SCRIPTS = SKILL_ROOT / "references" / "roles" / "model-reviewer" / "scripts"
sys.path.insert(0, str(REVIEWER_SCRIPTS))
import red_team_gate
import result_registry_gate


MIN_SCHEMA_VERSION = (2, 1)
HARD_CONSTRAINT_KEYS = {
    "min_content_units", "min_equations", "min_figures", "min_tables",
    "min_body_pages", "max_body_pages", "min_total_pages", "max_total_pages",
    "max_file_size_mb", "file_size_artifact", "require_reference_section",
    "abstract_fill_min", "abstract_fill_max",
}
QUALITY_TARGET_KEYS = {
    "blocking", "body_pages", "body_page_tolerance", "min_body_pages",
    "max_body_pages", "abstract_fill_min", "abstract_fill_max",
    "min_content_units", "min_equations", "min_figures", "min_tables",
}
STRUCTURE_KEYS = {
    "required_markers", "figure_labels", "table_labels", "reference_headings",
    "require_reference_section", "appendix_heading_patterns",
    "excluded_figure_objects", "excluded_table_objects", "check_figure_numbering",
    "check_table_numbering", "check_numeric_reference_bijection",
}
LAYOUT_KEYS = {
    "abstract_page", "abstract_heading_pattern", "abstract_end_pattern",
    "abstract_end_required", "body_start_page", "body_end_page",
    "appendix_required", "appendix_heading_pattern", "usable_bottom_margin_pt",
}


def _unknown_key_errors(value: dict, allowed: set[str], label: str) -> list[str]:
    return [f"{label} 含未识别字段: {key}" for key in sorted(set(value) - allowed)]


def _number(value, label: str, *, integer=False, minimum=None, maximum=None) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, (int,) if integer else (int, float)):
        return [f"{label} 必须为{'整数' if integer else '数值'}"]
    if not math.isfinite(float(value)):
        return [f"{label} 必须为有限数值"]
    if minimum is not None and value < minimum:
        return [f"{label} 不能小于 {minimum}"]
    if maximum is not None and value > maximum:
        return [f"{label} 不能大于 {maximum}"]
    return []


def _constraint_errors(hard: dict, quality: dict) -> list[str]:
    """拒绝拼错字段、非法范围及上下限倒置，避免约束被静默忽略。"""
    errors = _unknown_key_errors(hard, HARD_CONSTRAINT_KEYS, "official_rules.hard_constraints")
    errors.extend(_unknown_key_errors(quality, QUALITY_TARGET_KEYS, "quality_target"))
    hard_int_minimums = {
        "min_content_units": 0, "min_equations": 0, "min_figures": 0, "min_tables": 0,
        "min_body_pages": 1, "max_body_pages": 1, "min_total_pages": 1, "max_total_pages": 1,
    }
    quality_int_minimums = {
        "body_pages": 1, "body_page_tolerance": 0, "min_body_pages": 1,
        "max_body_pages": 1, "min_content_units": 0, "min_equations": 0,
        "min_figures": 0, "min_tables": 0,
    }
    for key, minimum in hard_int_minimums.items():
        if key in hard:
            errors.extend(_number(hard[key], f"official_rules.hard_constraints.{key}", integer=True, minimum=minimum))
    for key, minimum in quality_int_minimums.items():
        if key in quality:
            errors.extend(_number(quality[key], f"quality_target.{key}", integer=True, minimum=minimum))
    for owner, mapping in (("official_rules.hard_constraints", hard), ("quality_target", quality)):
        for key in ("abstract_fill_min", "abstract_fill_max"):
            if key in mapping:
                errors.extend(_number(mapping[key], f"{owner}.{key}", minimum=0, maximum=1))
    if "max_file_size_mb" in hard:
        errors.extend(_number(hard["max_file_size_mb"], "official_rules.hard_constraints.max_file_size_mb", minimum=0))
        if hard.get("max_file_size_mb") == 0:
            errors.append("official_rules.hard_constraints.max_file_size_mb 必须大于 0")
    if "require_reference_section" in hard and not isinstance(hard["require_reference_section"], bool):
        errors.append("official_rules.hard_constraints.require_reference_section 必须为布尔值")
    if "file_size_artifact" in hard and hard["file_size_artifact"] not in {"candidate", "rendered_pdf"}:
        errors.append("official_rules.hard_constraints.file_size_artifact 只能是 candidate 或 rendered_pdf")
    for owner, mapping, pairs in (
        ("official_rules.hard_constraints", hard, (("min_body_pages", "max_body_pages"), ("min_total_pages", "max_total_pages"), ("abstract_fill_min", "abstract_fill_max"))),
        ("quality_target", quality, (("min_body_pages", "max_body_pages"), ("abstract_fill_min", "abstract_fill_max"))),
    ):
        for lower, upper in pairs:
            if lower in mapping and upper in mapping:
                lower_value = mapping[lower]
                upper_value = mapping[upper]
                values_are_numeric = all(
                    isinstance(value, (int, float)) and not isinstance(value, bool)
                    for value in (lower_value, upper_value)
                )
                if values_are_numeric and lower_value > upper_value:
                    errors.append(f"{owner}.{lower} 不能大于 {owner}.{upper}")
    return errors


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_matches(path: Path, expected: str) -> bool:
    return _sha256(path).lower() == str(expected).lower()


def _schema_at_least(value, minimum=MIN_SCHEMA_VERSION) -> bool:
    try:
        parts = tuple(int(item) for item in str(value).split("."))
        return parts >= minimum
    except (TypeError, ValueError):
        return False


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _project_root(manifest: dict, manifest_path: Path) -> Path:
    configured = manifest.get("project_root")
    if configured:
        path = Path(str(configured))
        if not path.is_absolute():
            path = manifest_path.parent / path
        return path.resolve()
    parent = manifest_path.resolve().parent
    return parent.parent if parent.name.lower() == "results" else parent


def _resolve_path(value, project_root: Path) -> Path:
    path = Path(str(value))
    if not path.is_absolute():
        path = project_root / path
    resolved = path.resolve()
    if not _is_within(resolved, project_root):
        raise ValueError(f"发布证据路径越出 PROJECT_ROOT: {value}")
    if _is_within(resolved, SKILL_ROOT):
        raise ValueError(f"发布证据不能位于 SKILL_ROOT: {value}")
    return resolved


def _layout_errors(layout) -> list[str]:
    if not isinstance(layout, dict):
        return ["layout 必须为对象"]
    errors = _unknown_key_errors(layout, LAYOUT_KEYS, "layout")
    for key in ("abstract_heading_pattern", "abstract_end_pattern"):
        pattern = layout.get(key)
        if not isinstance(pattern, str) or not pattern.strip():
            errors.append(f"layout.{key} 必须是非空正则表达式")
        else:
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(f"layout.{key} 正则表达式无效: {exc}")
    appendix_pattern = layout.get("appendix_heading_pattern")
    if appendix_pattern is not None:
        if not isinstance(appendix_pattern, str) or not appendix_pattern.strip():
            errors.append("layout.appendix_heading_pattern 必须为非空正则表达式或省略")
        else:
            try:
                re.compile(appendix_pattern)
            except re.error as exc:
                errors.append(f"layout.appendix_heading_pattern 正则表达式无效: {exc}")
    for key in ("abstract_end_required", "appendix_required"):
        if key in layout and not isinstance(layout[key], bool):
            errors.append(f"layout.{key} 必须为布尔值")
    if layout.get("abstract_end_required", True) is not True:
        errors.append("摘要语义审计必须识别结束标记，layout.abstract_end_required 不能设为 false")
    if "usable_bottom_margin_pt" in layout:
        errors.extend(_number(layout["usable_bottom_margin_pt"], "layout.usable_bottom_margin_pt", minimum=0))
    return errors


def _structure_errors(structure) -> list[str]:
    if not isinstance(structure, dict):
        return ["structure 必须为对象"]
    errors = _unknown_key_errors(structure, STRUCTURE_KEYS, "structure")
    for key in (
        "required_markers", "figure_labels", "table_labels", "reference_headings",
        "appendix_heading_patterns",
    ):
        if key not in structure:
            continue
        value = structure[key]
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            errors.append(f"structure.{key} 必须为字符串数组")
            continue
        if key == "appendix_heading_patterns":
            for pattern in value:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    errors.append(f"structure.{key} 含无效正则表达式 {pattern!r}: {exc}")
    for key in (
        "require_reference_section", "check_figure_numbering", "check_table_numbering",
        "check_numeric_reference_bijection",
    ):
        if key in structure and not isinstance(structure[key], bool):
            errors.append(f"structure.{key} 必须为布尔值")
    for key in ("excluded_figure_objects", "excluded_table_objects"):
        if key in structure:
            errors.extend(_number(structure[key], f"structure.{key}", integer=True, minimum=0))
    return errors


def _publish_files(pairs: list[tuple[Path, Path]], *, overwrite: bool) -> None:
    """先准备全部临时文件，再提交；覆盖失败时恢复既有最终稿。"""
    token = uuid.uuid4().hex
    staged: list[tuple[Path, Path]] = []
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{token}.tmp")
            shutil.copy2(source, temporary)
            staged.append((temporary, target))
        if overwrite:
            for _, target in staged:
                if target.exists():
                    backup = target.with_name(f".{target.name}.{token}.bak")
                    target.replace(backup)
                    backups[target] = backup
        for temporary, target in staged:
            temporary.replace(target)
            published.append(target)
    except Exception:
        for target in reversed(published):
            if target not in backups:
                target.unlink(missing_ok=True)
        for target, backup in backups.items():
            if backup.exists():
                backup.replace(target)
        raise
    else:
        for backup in backups.values():
            backup.unlink(missing_ok=True)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def _artifact_errors(name: str, spec, project_root: Path, expected_path: Path | None = None):
    errors = []
    if not isinstance(spec, dict):
        return None, [f"发布清单缺少 artifacts.{name}"]
    if not spec.get("path") or not spec.get("sha256"):
        return None, [f"artifacts.{name} 必须记录 path 与 sha256"]
    try:
        path = _resolve_path(spec["path"], project_root)
    except Exception as exc:
        return None, [str(exc)]
    if expected_path is not None and path != expected_path.resolve():
        errors.append(f"命令行 {name} 路径与发布清单不一致")
    if not path.is_file():
        errors.append(f"发布证据文件不存在: artifacts.{name}={path}")
    elif not _hash_matches(path, spec["sha256"]):
        errors.append(f"artifacts.{name} 的 SHA-256 与发布清单不一致")
    return path, errors


validate_result_registry = result_registry_gate.validate


def _abstract_text(doc: Document, layout=None) -> str:
    layout = layout or {}
    heading_pattern = layout.get("abstract_heading_pattern")
    end_pattern = layout.get("abstract_end_pattern")
    if not heading_pattern or not end_pattern:
        raise ValueError("layout 必须声明 abstract_heading_pattern 与 abstract_end_pattern 以界定摘要原文")
    collecting = False
    ended = False
    parts = []
    for value in pf._document_texts(doc):
        if re.search(heading_pattern, value, re.IGNORECASE):
            collecting = True
            continue
        if collecting and end_pattern and re.search(end_pattern, value, re.IGNORECASE):
            ended = True
            break
        if collecting and value:
            parts.append(value)
    if not collecting:
        raise ValueError("候选稿中未识别到摘要起始标记")
    if not ended:
        raise ValueError("候选稿中未识别到摘要结束标记，无法防止正文证据误入摘要审计")
    return "\n".join(parts)


def _pdf_metrics(pdf_path: Path, layout: dict) -> dict:
    """按发布清单声明的分页结构测量 PDF，不假定摘要在首页或必须有附录。"""
    if not isinstance(layout, dict):
        raise ValueError("发布清单缺少 layout 对象")
    body_start = layout.get("body_start_page")
    if isinstance(body_start, bool) or not isinstance(body_start, int) or body_start < 1:
        raise ValueError("layout.body_start_page 必须是从 1 开始的正整数")
    appendix_required = layout.get("appendix_required", False)
    if not isinstance(appendix_required, bool):
        raise ValueError("layout.appendix_required 必须为布尔值")
    appendix_pattern = layout.get("appendix_heading_pattern")
    if appendix_required and not appendix_pattern:
        raise ValueError("要求附录时必须声明 layout.appendix_heading_pattern")

    with fitz.open(pdf_path) as pdf:
        if not pdf.page_count:
            raise ValueError("渲染 PDF 为空")
        appendix_start = None
        if appendix_pattern:
            for index, page in enumerate(pdf):
                text = page.get_text("text")
                if re.search(appendix_pattern, text, re.IGNORECASE | re.MULTILINE):
                    appendix_start = index + 1
                    break
        if appendix_required and appendix_start is None:
            raise ValueError("渲染 PDF 中未识别到发布清单要求的附录")

        explicit_body_end = layout.get("body_end_page")
        if explicit_body_end is not None and (
            isinstance(explicit_body_end, bool)
            or not isinstance(explicit_body_end, int)
            or explicit_body_end < body_start
        ):
            raise ValueError("layout.body_end_page 必须是不早于正文起始页的整数或 null")
        body_end = explicit_body_end
        if body_end is None:
            body_end = appendix_start - 1 if appendix_start is not None else pdf.page_count
        if body_end > pdf.page_count:
            raise ValueError("layout.body_end_page 超出渲染 PDF 总页数")
        if appendix_start is not None and body_end >= appendix_start:
            raise ValueError("正文结束页与识别到的附录页重叠")

        fill_ratio = None
        abstract_page_number = layout.get("abstract_page")
        if abstract_page_number is not None:
            if (
                isinstance(abstract_page_number, bool)
                or not isinstance(abstract_page_number, int)
                or not 1 <= abstract_page_number <= pdf.page_count
            ):
                raise ValueError("layout.abstract_page 必须是有效页码或 null")
            page = pdf[abstract_page_number - 1]
            blocks = [block for block in page.get_text("blocks") if str(block[4]).strip()]
            heading_pattern = layout.get("abstract_heading_pattern")
            if not heading_pattern:
                raise ValueError("测量摘要页时必须声明 layout.abstract_heading_pattern")
            title_block = next(
                (block for block in blocks if re.search(heading_pattern, str(block[4]).strip(), re.IGNORECASE)),
                None,
            )
            if title_block is None:
                raise ValueError(f"无法从第 {abstract_page_number} 页识别摘要标题")
            end_pattern = layout.get("abstract_end_pattern")
            end_block = None
            if end_pattern:
                end_block = next(
                    (
                        block
                        for block in blocks
                        if block[1] > title_block[3]
                        and re.search(end_pattern, str(block[4]).strip(), re.IGNORECASE)
                    ),
                    None,
                )
                if layout.get("abstract_end_required", True) and end_block is None:
                    raise ValueError(f"无法从第 {abstract_page_number} 页识别摘要结束标记")
            bottom_margin = float(layout.get("usable_bottom_margin_pt", 72.0))
            usable_bottom = page.rect.height - bottom_margin
            content_limit = end_block[1] if end_block is not None else usable_bottom
            content_blocks = [
                block
                for block in blocks
                if block[1] > title_block[3]
                and block[1] < content_limit
                and block[3] <= usable_bottom + 1.0
            ]
            if not content_blocks:
                raise ValueError("摘要正文为空或无法测量")
            content_bottom = max(block[3] for block in content_blocks)
            fill_ratio = (content_bottom - title_block[3]) / max(1.0, usable_bottom - title_block[3])

        return {
            "total_pages": pdf.page_count,
            "abstract_page": abstract_page_number,
            "appendix_start_page": appendix_start,
            "body_start_page": body_start,
            "body_end_page": body_end,
            "body_pages": body_end - body_start + 1,
            "abstract_fill_ratio": fill_ratio,
        }


def evaluate_release(manifest: dict, metrics: dict, abstract: str, warnings=None) -> list[str]:
    """评估规则、质量目标和动态摘要审计；质量非阻断项写入 warnings。"""
    errors = []
    warnings = warnings if warnings is not None else []
    if not _schema_at_least(manifest.get("schema_version")):
        errors.append("发布清单 schema_version 必须不低于 2.1")
    if not str(manifest.get("competition", "")).strip():
        errors.append("发布清单缺少 competition")
    if not str(manifest.get("edition", "")).strip():
        errors.append("发布清单缺少 edition")

    rules = manifest.get("official_rules", {})
    if not isinstance(rules, dict) or not rules.get("source") or not rules.get("verified_date"):
        errors.append("未锁定当届官方规则来源与核验日期")
        rules = rules if isinstance(rules, dict) else {}
    elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(rules.get("verified_date"))):
        errors.append("official_rules.verified_date 必须使用 YYYY-MM-DD")
    if not isinstance(rules.get("hard_constraints"), dict):
        errors.append("official_rules.hard_constraints 必须显式记录为对象；无数值硬约束时使用空对象")
    if rules.get("submission_format") not in {"docx", "pdf", "both"}:
        errors.append("official_rules.submission_format 必须为 docx、pdf 或 both")

    template = manifest.get("template", {})
    if not isinstance(template, dict) or template.get("authority") not in {"official", "user_provided", "fallback"}:
        errors.append("模板 authority 必须为 official、user_provided 或 fallback")
        template = template if isinstance(template, dict) else {}
    if not template.get("path") or not template.get("sha256"):
        errors.append("未记录模板路径与 SHA-256")
    if template.get("authority") == "fallback":
        if template.get("user_approved_fallback") is not True:
            errors.append("使用备用模板但没有用户明确批准")
        if not str(template.get("approval_evidence", "")).strip():
            errors.append("使用备用模板但未记录用户批准证据")

    renderer = manifest.get("renderer", {})
    if not isinstance(renderer, dict) or not str(renderer.get("name", "")).strip() or not str(renderer.get("version", "")).strip():
        errors.append("未锁定实际渲染器名称与版本")

    quality = manifest.get("quality_target", {})
    if not isinstance(quality, dict):
        errors.append("quality_target 必须为对象")
        quality = {}
    quality_keys = {
        "body_pages", "min_body_pages", "max_body_pages", "abstract_fill_min",
        "abstract_fill_max", "min_content_units", "min_equations", "min_figures", "min_tables",
        "body_page_tolerance",
    }
    has_quality_target = any(key in quality for key in quality_keys)
    if has_quality_target and not isinstance(quality.get("blocking"), bool):
        errors.append("配置质量目标时必须显式声明 quality_target.blocking")
    quality_blocking = quality.get("blocking", True)
    hard_constraints = rules.get("hard_constraints", {}) if isinstance(rules.get("hard_constraints"), dict) else {}
    errors.extend(_constraint_errors(hard_constraints, quality))

    def quality_issue(message):
        (errors if quality_blocking else warnings).append(message)

    try:
        if "body_pages" in quality:
            target_pages = int(quality["body_pages"])
            tolerance = int(quality.get("body_page_tolerance", 0))
            if abs(int(metrics["body_pages"]) - target_pages) > tolerance:
                quality_issue(
                    f"正文实测 {metrics['body_pages']} 页，未达到 {target_pages}±{tolerance} 页质量目标"
                )
        if "min_body_pages" in quality and int(metrics["body_pages"]) < int(quality["min_body_pages"]):
            quality_issue(f"正文实测 {metrics['body_pages']} 页，低于质量下限 {quality['min_body_pages']} 页")
        if "max_body_pages" in quality and int(metrics["body_pages"]) > int(quality["max_body_pages"]):
            quality_issue(f"正文实测 {metrics['body_pages']} 页，超过质量上限 {quality['max_body_pages']} 页")
        fill_keys = {"abstract_fill_min", "abstract_fill_max"} & set(quality)
        if fill_keys and metrics.get("abstract_fill_ratio") is None:
            quality_issue("发布清单配置了摘要填充率目标，但 layout.abstract_page 未提供可测指标")
        elif fill_keys:
            fill = float(metrics["abstract_fill_ratio"])
            if "abstract_fill_min" in quality and fill < float(quality["abstract_fill_min"]):
                quality_issue(f"摘要有效填充率 {fill:.1%} 低于质量下限 {float(quality['abstract_fill_min']):.0%}")
            if "abstract_fill_max" in quality and fill > float(quality["abstract_fill_max"]):
                quality_issue(f"摘要有效填充率 {fill:.1%} 超过质量上限 {float(quality['abstract_fill_max']):.0%}")
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"quality_target 数值无效: {exc}")

    audit = manifest.get("abstract_audit", {})
    if not isinstance(audit, dict):
        errors.append("abstract_audit 必须为对象")
        audit = {}
    required_items = audit.get("required_items")
    items = audit.get("items")
    if not isinstance(required_items, list) or not required_items or not all(
        isinstance(item, str) and item.strip() for item in required_items
    ):
        errors.append("abstract_audit.required_items 必须是动态声明的非空数组")
        required_items = []
    elif len(set(required_items)) != len(required_items):
        errors.append("abstract_audit.required_items 存在重复项")
    if not isinstance(items, dict):
        errors.append("abstract_audit.items 必须为对象")
        items = {}
    evidence_owners: dict[str, str] = {}
    placeholder_pattern = re.compile(r"待补充|替换为|摘要证据|\b(?:todo|tbd|placeholder)\b", re.IGNORECASE)
    for item_id in required_items:
        item = items.get(item_id, {})
        if not isinstance(item, dict):
            errors.append(f"摘要语义审计项必须为对象：{item_id}")
            continue
        if not str(item.get("requirement", "")).strip():
            errors.append(f"摘要语义审计项缺少 requirement：{item_id}")
        if item.get("status") != "pass":
            errors.append(f"摘要语义审计未通过：{item_id}")
            continue
        evidence = item.get("evidence", [])
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(snippet, str) and snippet.strip() for snippet in evidence
        ):
            errors.append(f"摘要语义审计证据格式无效：{item_id}")
            continue
        normalized_evidence = [re.sub(r"\s+", " ", snippet.strip()) for snippet in evidence]
        if len(set(normalized_evidence)) != len(normalized_evidence):
            errors.append(f"摘要语义审计项内存在重复证据：{item_id}")
        for snippet, normalized in zip(evidence, normalized_evidence):
            content_units = len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", normalized))
            if content_units < 6 or placeholder_pattern.search(normalized):
                errors.append(f"摘要语义审计证据过短或仍是占位文本：{item_id}")
            if snippet not in abstract:
                errors.append(f"摘要语义审计证据未出现在摘要中：{item_id}")
            owner = evidence_owners.get(normalized)
            if owner is not None and owner != item_id:
                errors.append(f"摘要必答项 {owner} 与 {item_id} 复用了完全相同的证据片段")
            else:
                evidence_owners[normalized] = item_id
    return errors


def run(
    manifest_path: Path,
    candidate: Path,
    rendered_pdf: Path,
    output: Path,
    *,
    submission_pdf_output: Path | None = None,
    overwrite=False,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("发布清单顶层必须为对象")
    manifest_path = manifest_path.resolve()
    project_root = _project_root(manifest, manifest_path)
    candidate = candidate.resolve()
    rendered_pdf = rendered_pdf.resolve()
    output = output.resolve()
    submission_pdf_output = submission_pdf_output.resolve() if submission_pdf_output else None
    if not project_root.is_dir():
        raise ValueError(f"PROJECT_ROOT 不存在: {project_root}")
    if _is_within(project_root, SKILL_ROOT):
        raise ValueError("PROJECT_ROOT 不能位于 SKILL_ROOT 内部")
    if not _is_within(manifest_path, project_root) or _is_within(manifest_path, SKILL_ROOT):
        raise ValueError("发布清单必须位于 PROJECT_ROOT 内部且不能位于 SKILL_ROOT")
    protected_paths = [("候选稿", candidate), ("渲染 PDF", rendered_pdf), ("最终 DOCX", output)]
    if submission_pdf_output is not None:
        protected_paths.append(("最终 PDF", submission_pdf_output))
    for label, path in protected_paths:
        if not _is_within(path, project_root):
            raise ValueError(f"{label}必须位于 PROJECT_ROOT 内部: {path}")
        if _is_within(path, SKILL_ROOT):
            raise ValueError(f"{label}不能位于 SKILL_ROOT 内部: {path}")
    if candidate == output:
        raise ValueError("候选稿与最终稿路径必须不同")
    if output in {manifest_path, rendered_pdf}:
        raise ValueError("最终 DOCX 不能覆盖发布清单或渲染证据")
    if submission_pdf_output is not None and submission_pdf_output == rendered_pdf:
        raise ValueError("渲染抽检 PDF 与最终提交 PDF 路径必须不同")
    if submission_pdf_output is not None and submission_pdf_output in {manifest_path, candidate, output}:
        raise ValueError("最终 PDF 不能覆盖发布清单、候选稿或最终 DOCX")
    for label, path, suffix in (
        ("候选稿", candidate, ".docx"), ("渲染 PDF", rendered_pdf, ".pdf"),
        ("最终 DOCX", output, ".docx"),
    ):
        if path.suffix.lower() != suffix:
            raise ValueError(f"{label} 必须使用 {suffix} 扩展名: {path}")
    if submission_pdf_output is not None and submission_pdf_output.suffix.lower() != ".pdf":
        raise ValueError(f"最终 PDF 必须使用 .pdf 扩展名: {submission_pdf_output}")
    if output.exists() and not overwrite:
        raise FileExistsError(f"最终稿已存在；如确认覆盖请显式使用 --overwrite: {output}")
    if submission_pdf_output is not None and submission_pdf_output.exists() and not overwrite:
        raise FileExistsError(f"最终 PDF 已存在；如确认覆盖请显式使用 --overwrite: {submission_pdf_output}")

    errors = []
    warnings = []
    errors.extend(_layout_errors(manifest.get("layout")))
    errors.extend(_structure_errors(manifest.get("structure", {})))
    rules = manifest.get("official_rules", {})
    submission_format = rules.get("submission_format") if isinstance(rules, dict) else None
    if submission_format in {"pdf", "both"} and submission_pdf_output is None:
        errors.append("官方提交格式包含 PDF，但未提供 --submission-pdf-output")
    if submission_format == "docx" and submission_pdf_output is not None:
        errors.append("官方提交格式为 docx，不应提供 --submission-pdf-output")
    artifacts = manifest.get("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
        errors.append("发布清单 artifacts 必须为对象")
    candidate_path, artifact_errors = _artifact_errors(
        "candidate", artifacts.get("candidate"), project_root, candidate
    )
    errors.extend(artifact_errors)
    pdf_path, artifact_errors = _artifact_errors(
        "rendered_pdf", artifacts.get("rendered_pdf"), project_root, rendered_pdf
    )
    errors.extend(artifact_errors)
    registry_path, artifact_errors = _artifact_errors(
        "result_registry", artifacts.get("result_registry"), project_root
    )
    errors.extend(artifact_errors)
    audit_path, artifact_errors = _artifact_errors(
        "red_team_audit", artifacts.get("red_team_audit"), project_root
    )
    errors.extend(artifact_errors)

    candidate_spec = artifacts.get("candidate", {}) if isinstance(artifacts.get("candidate"), dict) else {}
    rendered_spec = artifacts.get("rendered_pdf", {}) if isinstance(artifacts.get("rendered_pdf"), dict) else {}
    candidate_hash = candidate_spec.get("sha256")
    if not candidate_hash or str(rendered_spec.get("source_candidate_sha256", "")).lower() != str(candidate_hash).lower():
        errors.append("artifacts.rendered_pdf.source_candidate_sha256 未锁定当前候选稿")

    template = manifest.get("template", {})
    template_path = None
    if isinstance(template, dict) and template.get("path"):
        try:
            template_path = _resolve_path(template["path"], project_root)
            if not template_path.is_file():
                errors.append("清单中的模板文件不存在")
            elif not _hash_matches(template_path, template.get("sha256", "")):
                errors.append("模板 SHA-256 与清单不一致")
        except Exception as exc:
            errors.append(str(exc))

    locked_inputs = {
        path for path in (manifest_path, candidate_path, pdf_path, registry_path, audit_path, template_path)
        if path is not None
    }
    if output in locked_inputs:
        errors.append("最终 DOCX 路径与已锁定输入或证据文件冲突")
    if submission_pdf_output is not None and submission_pdf_output in locked_inputs:
        errors.append("最终 PDF 路径与已锁定输入或证据文件冲突")

    if registry_path is not None and registry_path.is_file():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            errors.extend(result_registry_gate.validate(registry, project_root=project_root))
        except Exception as exc:
            errors.append(f"result_registry.json 无法读取: {exc}")
    if audit_path is not None and audit_path.is_file():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            if audit.get("status") != "pass":
                errors.append("red_team_audit.json 顶层状态不是 pass")
            errors.extend(red_team_gate.validate(audit, project_root=project_root))
        except Exception as exc:
            errors.append(f"red_team_audit.json 无法通过评审门: {exc}")

    if errors or candidate_path is None or pdf_path is None:
        return {
            "status": "fail",
            "metrics": {},
            "warnings": warnings,
            "errors": list(dict.fromkeys(errors)),
        }

    layout = manifest.get("layout", {})
    doc = Document(candidate_path)
    abstract = _abstract_text(doc, layout)
    metrics = _pdf_metrics(pdf_path, layout)
    errors.extend(evaluate_release(manifest, metrics, abstract, warnings))

    rules = manifest.get("official_rules", {})
    hard_constraints = rules.get("hard_constraints", {}) if isinstance(rules, dict) else {}
    quality_targets = manifest.get("quality_target", {})
    structure = manifest.get("structure", {})
    if not isinstance(structure, dict):
        structure = {}
        errors.append("structure 必须为对象")
    file_size_artifact = hard_constraints.get("file_size_artifact", "rendered_pdf")
    if file_size_artifact not in {"candidate", "rendered_pdf"}:
        errors.append("official_rules.hard_constraints.file_size_artifact 只能是 candidate 或 rendered_pdf")
        file_size_bytes = None
    else:
        file_size_bytes = (
            candidate_path.stat().st_size
            if file_size_artifact == "candidate"
            else pdf_path.stat().st_size
        )
    structure_issues = pf.validate_paper_structure(
        doc,
        contest=manifest.get("contest_profile", "custom"),
        rendered_pages=metrics["total_pages"],
        rendered_body_pages=metrics["body_pages"],
        abstract_fill_ratio=metrics["abstract_fill_ratio"],
        electronic_file_size_bytes=file_size_bytes,
        hard_constraints=hard_constraints,
        quality_targets=quality_targets,
        required_markers=structure.get("required_markers"),
        figure_labels=structure.get("figure_labels"),
        table_labels=structure.get("table_labels"),
        reference_headings=structure.get("reference_headings"),
        require_reference_section=structure.get("require_reference_section"),
        appendix_heading_patterns=structure.get("appendix_heading_patterns"),
        excluded_figure_objects=structure.get("excluded_figure_objects", 0),
        excluded_table_objects=structure.get("excluded_table_objects", 0),
        check_figure_numbering=structure.get("check_figure_numbering", True),
        check_table_numbering=structure.get("check_table_numbering", True),
        check_numeric_reference_bijection=structure.get("check_numeric_reference_bijection", True),
    )
    quality_blocking = quality_targets.get("blocking", True) if isinstance(quality_targets, dict) else True
    for issue in structure_issues:
        if issue.startswith("预警：") and not quality_blocking:
            warnings.append(issue)
        else:
            errors.append(issue)
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    result = {
        "status": "pass" if not errors else "fail",
        "metrics": metrics,
        "warnings": warnings,
        "errors": errors,
    }
    if errors:
        return result
    publication_pairs = [(candidate_path, output)]
    if submission_pdf_output is not None:
        publication_pairs.append((pdf_path, submission_pdf_output))
    _publish_files(publication_pairs, overwrite=overwrite)
    result["output"] = str(output)
    result["output_sha256"] = _sha256(output)
    if submission_pdf_output is not None:
        result["submission_pdf"] = str(submission_pdf_output)
        result["submission_pdf_sha256"] = _sha256(submission_pdf_output)
    if submission_format == "pdf":
        result["submission_files"] = [str(submission_pdf_output)]
    elif submission_format == "both":
        result["submission_files"] = [str(output), str(submission_pdf_output)]
    else:
        result["submission_files"] = [str(output)]
    result["verified_artifacts"] = {
        "candidate": _sha256(candidate_path),
        "rendered_pdf": _sha256(pdf_path),
        "result_registry": _sha256(registry_path),
        "red_team_audit": _sha256(audit_path),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--rendered-pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--submission-pdf-output", type=Path, help="官方要求 PDF 时的最终提交文件路径")
    parser.add_argument("--overwrite", action="store_true", help="显式允许覆盖已存在的最终稿")
    args = parser.parse_args()
    try:
        result = run(
            args.manifest,
            args.candidate,
            args.rendered_pdf,
            args.output,
            submission_pdf_output=args.submission_pdf_output,
            overwrite=args.overwrite,
        )
    except Exception as exc:
        result = {"status": "fail", "metrics": {}, "warnings": [], "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
