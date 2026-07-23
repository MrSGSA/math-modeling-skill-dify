from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REQUIRED_CHECKS = {
    "domain_and_visibility",
    "boundary_and_extremes",
    "units_and_invariants",
    "set_and_time_measure",
    "marginal_contribution",
    "discrete_assignment",
    "stochastic_stability",
    "grid_convergence",
    "cross_output_consistency",
}
CHECK_STATES = {"pass", "fail", "pending", "na"}
CLAIM_STRENGTHS = {"feasible", "budget_best", "discrete_global", "global"}


def nonempty_list(value) -> bool:
    return isinstance(value, list) and any(str(item).strip() for item in value)


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    for key in ("schema_version", "project", "status", "applicable_checks", "claims", "findings"):
        if key not in data:
            errors.append(f"缺少顶层字段: {key}")

    if data.get("status") != "pass":
        errors.append("顶层 status 必须为 pass")

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
        for finding in findings:
            if not isinstance(finding, dict):
                errors.append("findings 中存在非对象条目")
                continue
            if finding.get("severity") in {"critical", "major"} and finding.get("status") != "closed":
                errors.append(f"存在未关闭的{finding.get('severity')}发现: {finding.get('id', 'unknown')}")
    else:
        errors.append("findings 必须为数组")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验数学建模红队评审门审计文件")
    parser.add_argument("audit_json", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.audit_json.read_text(encoding="utf-8"))
    except Exception as exc:
        print(json.dumps({"status": "fail", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 2
    errors = validate(data)
    result = {"status": "pass" if not errors else "fail", "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
