from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path


SOURCE_FILE_PATTERN = re.compile(
    r"^(.*?\.(?:json|csv|tsv|xlsx|xls|txt|log|md|npy|npz|mat|png|jpe?g|svg|pdf))"
    r"(?::.*|#.*)?$",
    re.IGNORECASE,
)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _entries(data):
    """读取推荐结构，并兼容迁移中的列表或稳定键映射结构。"""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("entries"), list):
        return data["entries"]
    if isinstance(data.get("values"), list):
        return data["values"]
    ignored = {"schema_version", "project", "meta", "generated_from"}
    candidates = [(key, value) for key, value in data.items() if key not in ignored]
    if candidates and all(isinstance(value, dict) for _, value in candidates):
        return [dict(value, key=key) for key, value in candidates]
    return None


def validate(data, project_root: Path | None = None) -> list[str]:
    """校验稳定键、值、单位、精度、来源和生成命令。"""
    entries = _entries(data)
    if not entries:
        return ["result_registry.json 必须包含非空结果条目"]
    errors = []
    if isinstance(data, dict) and "values" in data:
        errors.append("结果注册表使用旧容器 values；请迁移为 entries 或稳定键映射")
    seen = set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            errors.append(f"结果注册表第 {index} 项必须为对象")
            continue
        key = str(entry.get("key", "")).strip()
        label = key or str(index)
        if not key:
            errors.append(f"结果注册表第 {index} 项缺少稳定 key")
        elif key in seen:
            errors.append(f"结果注册表 key 重复: {key}")
        seen.add(key)
        if "value" not in entry or entry.get("value") is None:
            errors.append(f"结果 {label} 缺少 value")
        elif isinstance(entry.get("value"), float) and not math.isfinite(entry["value"]):
            errors.append(f"结果 {label} 的 value 不能是 NaN 或无穷大")
        if not str(entry.get("unit", "")).strip():
            errors.append(f"结果 {label} 缺少 unit；无量纲量请写 1")
        precision = entry.get("precision")
        if isinstance(precision, bool) or not isinstance(precision, int) or precision < 0:
            errors.append(f"结果 {label} 的 precision 必须是非负整数小数位数")
        source = str(entry.get("source", "")).strip()
        if not source:
            errors.append(f"结果 {label} 缺少 source")
        else:
            match = SOURCE_FILE_PATTERN.match(source)
            if not match:
                errors.append(f"结果 {label} 的 source 必须引用 PROJECT_ROOT 内证据文件，可在 : 或 # 后附定位说明")
            elif project_root is not None:
                path = Path(match.group(1))
                root = Path(project_root).resolve()
                resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
                if not _is_within(resolved, root):
                    errors.append(f"结果 {label} 的 source 越出 PROJECT_ROOT: {source}")
                elif not resolved.is_file():
                    errors.append(f"结果 {label} 的 source 文件不存在: {source}")
        if not str(entry.get("generated_by", "")).strip():
            if str(entry.get("generator", "")).strip():
                errors.append(f"结果 {label} 使用旧字段 generator；请迁移为 generated_by")
            else:
                errors.append(f"结果 {label} 缺少 generated_by")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验数学建模 result_registry.json")
    parser.add_argument("registry_json", type=Path)
    parser.add_argument("--project-root", type=Path, help="项目根目录；默认从 results/ 上级推断")
    parser.add_argument("--schema-only", action="store_true", help="只校验字段；仅用于测试与迁移")
    args = parser.parse_args()
    try:
        data = json.loads(args.registry_json.read_text(encoding="utf-8"))
        registry_path = args.registry_json.resolve()
        inferred_root = registry_path.parent.parent if registry_path.parent.name.lower() == "results" else registry_path.parent
        project_root = (args.project_root or inferred_root).resolve()
        errors = validate(data, None if args.schema_only else project_root)
    except Exception as exc:
        errors = [str(exc)]
    result = {"status": "pass" if not errors else "fail", "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
