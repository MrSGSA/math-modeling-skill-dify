from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REQUIRED_CHECKS = {
    "data_integrity_and_leakage",
    "domain_and_visibility",
    "boundary_and_extremes",
    "units_and_invariants",
    "parameter_semantics_and_constraints",
    "objective_metric_alignment",
    "validation_and_uncertainty",
    "implementation_and_oracle",
    "set_and_time_measure",
    "marginal_contribution",
    "discrete_assignment",
    "stochastic_stability",
    "grid_convergence",
    "cross_output_consistency",
}
ALWAYS_APPLICABLE_CHECKS = {
    "data_integrity_and_leakage",
    "objective_metric_alignment",
    "implementation_and_oracle",
    "cross_output_consistency",
}
CHECK_STATES = {"pass", "fail", "pending", "na"}
CLAIM_STRENGTHS = {"feasible", "budget_best", "discrete_global", "global"}
PARAMETER_STATES = {"pass", "fail", "pending"}
FINDING_SEVERITIES = {"critical", "major", "minor"}
FINDING_STATES = {"open", "closed"}
ORACLE_TYPES = {
    "analytic",
    "hand_calculation",
    "enumeration",
    "exact_solver",
    "independent_implementation",
    "invariant",
    "metamorphic",
    "trusted_benchmark",
}
EVIDENCE_FILE_PATTERN = re.compile(
    r"^(.*?\.(?:json|csv|tsv|xlsx|xls|txt|log|md|npy|npz|mat|png|jpe?g|svg|pdf|docx|py|m))"
    r"(?::.*|#.*)?$",
    re.IGNORECASE,
)


def nonempty_list(value) -> bool:
    if not isinstance(value, list):
        return False
    return any(
        str(item.get("path", "")).strip() if isinstance(item, dict) else str(item).strip()
        for item in value
    )


def schema_at_least(value, minimum=(1, 3)) -> bool:
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


def _evidence_file(item):
    raw = item.get("path") if isinstance(item, dict) else item
    match = EVIDENCE_FILE_PATTERN.match(str(raw).strip())
    return match.group(1) if match else None


def validate_evidence_files(data, project_root: Path) -> list[str]:
    """要求所有审计 evidence 指向 PROJECT_ROOT 内实际存在的证据文件。"""
    root = project_root.resolve()
    errors = []

    def visit(value, trail):
        if isinstance(value, dict):
            for key, child in value.items():
                current = f"{trail}.{key}" if trail else key
                if key == "evidence" and isinstance(child, list):
                    for index, item in enumerate(child):
                        evidence_path = _evidence_file(item)
                        label = f"{current}[{index}]"
                        if not evidence_path:
                            errors.append(f"{label} 必须引用可核验的本地证据文件")
                            continue
                        path = Path(evidence_path)
                        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
                        if not _is_within(resolved, root):
                            errors.append(f"{label} 越出 PROJECT_ROOT: {evidence_path}")
                        elif not resolved.is_file():
                            errors.append(f"{label} 引用的证据文件不存在: {evidence_path}")
                else:
                    visit(child, current)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{trail}[{index}]")

    visit(data, "")
    return errors


def validate_parameter_audit(audit) -> list[str]:
    errors: list[str] = []
    if not isinstance(audit, dict):
        return ["parameter_audit 必须为对象"]
    state = audit.get("status")
    if state == "na":
        if not str(audit.get("reason", "")).strip():
            errors.append("parameter_audit 标为 na 但没有理由")
        return errors
    if state != "pass":
        errors.append(f"parameter_audit 状态必须为 pass 或有理由的 na: {state}")
        return errors
    if not str(audit.get("coverage_statement", "")).strip():
        errors.append("parameter_audit 缺少参数覆盖声明")
    parameters = audit.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        errors.append("parameter_audit.parameters 必须是非空数组")
        parameters = []
    required = (
        "name", "role", "unit", "expected_domain", "domain_basis",
        "observed_value_or_range", "observed_behavior",
    )
    seen: set[str] = set()
    for index, parameter in enumerate(parameters, 1):
        if not isinstance(parameter, dict):
            errors.append(f"第{index}个参数审计项必须为对象")
            continue
        name = str(parameter.get("name", "")).strip()
        if not name:
            errors.append(f"第{index}个参数审计项缺少 name")
        elif name in seen:
            errors.append(f"参数审计项名称重复: {name}")
        seen.add(name)
        for field in required:
            if not str(parameter.get(field, "")).strip():
                errors.append(f"参数 {name or index} 缺少 {field}")
        pstate = parameter.get("status")
        if pstate not in PARAMETER_STATES:
            errors.append(f"参数 {name or index} 状态无效: {pstate}")
        elif pstate != "pass":
            errors.append(f"参数 {name or index} 尚未通过: {pstate}")
        if not nonempty_list(parameter.get("evidence")):
            errors.append(f"参数 {name or index} 缺少证据")
        risk = parameter.get("boundary_risk")
        if not isinstance(risk, bool):
            errors.append(f"参数 {name or index} 的 boundary_risk 必须为布尔值")
        elif risk:
            refit = parameter.get("constraint_refit")
            if not isinstance(refit, dict):
                errors.append(f"参数 {name or index} 存在边界风险但缺少 constraint_refit")
            else:
                for field in ("unrestricted_result", "constrained_result",
                              "impact_on_core_outputs", "conclusion"):
                    if not str(refit.get(field, "")).strip():
                        errors.append(f"参数 {name or index} 的 constraint_refit 缺少 {field}")
                if not nonempty_list(refit.get("evidence")):
                    errors.append(f"参数 {name or index} 的 constraint_refit 缺少证据")
    cross = audit.get("cross_model_comparison")
    if not isinstance(cross, dict):
        errors.append("parameter_audit 缺少 cross_model_comparison")
    else:
        cstate = cross.get("status")
        if cstate == "pass" and not nonempty_list(cross.get("evidence")):
            errors.append("cross_model_comparison 通过但没有证据")
        elif cstate == "na" and not str(cross.get("reason", "")).strip():
            errors.append("cross_model_comparison 标为 na 但没有理由")
        elif cstate not in {"pass", "na"}:
            errors.append(f"cross_model_comparison 尚未通过: {cstate}")
    return errors


def validate_oracle_audit(check) -> list[str]:
    """要求实现校验包含能区分常见错误实现的独立 oracle 记录。"""
    errors: list[str] = []
    if not isinstance(check, dict) or check.get("status") != "pass":
        return errors
    oracle_audit = check.get("oracle_audit")
    if not isinstance(oracle_audit, dict):
        return ["implementation_and_oracle 通过但缺少 oracle_audit"]
    if not str(oracle_audit.get("coverage_statement", "")).strip():
        errors.append("oracle_audit 缺少核心例程覆盖声明")
    oracles = oracle_audit.get("oracles")
    if not isinstance(oracles, list) or not oracles:
        errors.append("oracle_audit.oracles 必须是非空数组")
        return errors

    seen: set[str] = set()
    for index, oracle in enumerate(oracles, 1):
        if not isinstance(oracle, dict):
            errors.append(f"第{index}个 oracle 必须为对象")
            continue
        oracle_id = str(oracle.get("id", "")).strip()
        if not oracle_id:
            errors.append(f"第{index}个 oracle 缺少 id")
        elif oracle_id in seen:
            errors.append(f"oracle id 重复: {oracle_id}")
        seen.add(oracle_id)
        oracle_type = oracle.get("type")
        if oracle_type not in ORACLE_TYPES:
            errors.append(f"oracle {oracle_id or index} 类型无效: {oracle_type}")
        for field in ("target", "discriminating_case", "expected", "observed"):
            if not str(oracle.get(field, "")).strip():
                errors.append(f"oracle {oracle_id or index} 缺少 {field}")
        if oracle.get("status") != "pass":
            errors.append(f"oracle {oracle_id or index} 尚未通过")
        if not nonempty_list(oracle.get("evidence")):
            errors.append(f"oracle {oracle_id or index} 缺少证据")
    return errors


def validate(data: dict, project_root: Path | None = None) -> list[str]:
    if not isinstance(data, dict):
        return ["red_team_audit.json 顶层必须为对象"]
    errors: list[str] = []
    for key in ("schema_version", "project", "status", "applicable_checks", "parameter_audit", "claims", "findings"):
        if key not in data:
            errors.append(f"缺少顶层字段: {key}")

    if not schema_at_least(data.get("schema_version")):
        errors.append("schema_version 必须不低于 1.3")

    if data.get("status") != "pass":
        errors.append("顶层 status 必须为 pass")
    if not str(data.get("project", "")).strip():
        errors.append("project 必须为非空项目标识")

    checks = data.get("applicable_checks", {})
    if not isinstance(checks, dict):
        errors.append("applicable_checks 必须为对象")
        checks = {}
    missing = sorted(REQUIRED_CHECKS - set(checks))
    if missing:
        errors.append("缺少检查项: " + ", ".join(missing))
    for name in sorted(REQUIRED_CHECKS & set(checks)):
        item = checks[name]
        if not isinstance(item, dict):
            errors.append(f"检查项 {name} 必须为对象")
            continue
        state = item.get("status")
        if state not in CHECK_STATES:
            errors.append(f"检查项 {name} 状态无效: {state}")
        elif state != "pass" and state != "na":
            errors.append(f"检查项 {name} 尚未通过: {state}")
        elif state == "pass" and not nonempty_list(item.get("evidence")):
            errors.append(f"检查项 {name} 通过但没有证据")
        elif state == "na" and not str(item.get("reason", "")).strip():
            errors.append(f"检查项 {name} 标为 na 但没有理由")
        if name in ALWAYS_APPLICABLE_CHECKS and state == "na":
            errors.append(f"完整建模发布中检查项 {name} 不得标为 na")

    errors.extend(validate_oracle_audit(checks.get("implementation_and_oracle")))

    parameter_check = checks.get("parameter_semantics_and_constraints", {})
    parameter_audit = data.get("parameter_audit", {})
    if isinstance(parameter_check, dict) and isinstance(parameter_audit, dict):
        if parameter_check.get("status") != parameter_audit.get("status"):
            errors.append("parameter_semantics_and_constraints 与 parameter_audit 状态不一致")

    claims = data.get("claims", [])
    if not isinstance(claims, list) or not claims:
        errors.append("claims 必须是非空数组")
        claims = []
    seen: set[str] = set()
    for index, claim in enumerate(claims, 1):
        if not isinstance(claim, dict):
            errors.append(f"第{index}条主张必须为对象")
            continue
        cid = str(claim.get("id", "")).strip()
        if not cid:
            errors.append(f"第{index}条主张缺少 id")
        elif cid in seen:
            errors.append(f"主张 id 重复: {cid}")
        seen.add(cid)
        if not str(claim.get("text", "")).strip():
            errors.append(f"主张 {cid or index} 缺少文本")
        strength = claim.get("strength")
        if strength not in CLAIM_STRENGTHS:
            errors.append(f"主张 {cid or index} 证据等级无效: {strength}")
        if claim.get("status") != "pass":
            errors.append(f"主张 {cid or index} 尚未通过")
        if not nonempty_list(claim.get("evidence")):
            errors.append(f"主张 {cid or index} 缺少证据")
        if not str(claim.get("search_scope", "")).strip():
            errors.append(f"主张 {cid or index} 缺少搜索/适用范围")
        if not str(claim.get("falsification", "")).strip():
            errors.append(f"主张 {cid or index} 缺少推翻条件")
        if strength == "global" and not str(claim.get("proof", "")).strip():
            errors.append(f"全局最优主张 {cid or index} 缺少严格证明")

    findings = data.get("findings", [])
    if isinstance(findings, list):
        finding_ids: set[str] = set()
        for index, finding in enumerate(findings, 1):
            if not isinstance(finding, dict):
                errors.append("findings 中存在非对象条目")
                continue
            finding_id = str(finding.get("id", "")).strip()
            if not finding_id:
                errors.append(f"第{index}条 finding 缺少 id")
            elif finding_id in finding_ids:
                errors.append(f"finding id 重复: {finding_id}")
            finding_ids.add(finding_id)
            severity = finding.get("severity")
            state = finding.get("status")
            if severity not in FINDING_SEVERITIES:
                errors.append(f"finding {finding_id or index} 严重度无效: {severity}")
            if state not in FINDING_STATES:
                errors.append(f"finding {finding_id or index} 状态无效: {state}")
            if severity in {"critical", "major"} and state != "closed":
                errors.append(f"存在未关闭的{severity}发现: {finding_id or 'unknown'}")
            if severity in {"critical", "major"} and state == "closed":
                if not str(finding.get("resolution", "")).strip():
                    errors.append(f"已关闭的{severity}发现 {finding_id or index} 缺少 resolution")
                if not nonempty_list(finding.get("evidence")):
                    errors.append(f"已关闭的{severity}发现 {finding_id or index} 缺少修复证据")
    else:
        errors.append("findings 必须为数组")
    errors.extend(validate_parameter_audit(parameter_audit))
    if project_root is not None:
        errors.extend(validate_evidence_files(data, Path(project_root)))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验数学建模红队评审门审计文件")
    parser.add_argument("audit_json", type=Path)
    parser.add_argument("--project-root", type=Path, help="项目根目录；默认从 results/ 上级推断")
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="仅检查结构，不检查 evidence 文件；只用于测试夹具和模板开发",
    )
    args = parser.parse_args()
    try:
        data = json.loads(args.audit_json.read_text(encoding="utf-8"))
    except Exception as exc:
        print(json.dumps({"status": "fail", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 2
    audit_path = args.audit_json.resolve()
    inferred_root = audit_path.parent.parent if audit_path.parent.name.lower() == "results" else audit_path.parent
    project_root = (args.project_root or inferred_root).resolve()
    errors = validate(data, None if args.schema_only else project_root)
    result = {
        "status": "pass" if not errors else "fail",
        "project_root": None if args.schema_only else str(project_root),
        "schema_only": args.schema_only,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
