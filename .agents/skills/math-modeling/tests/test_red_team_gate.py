import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "references"
    / "roles"
    / "model-reviewer"
    / "scripts"
    / "red_team_gate.py"
)
SPEC = importlib.util.spec_from_file_location("red_team_gate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def passing_audit():
    checks = {
        name: {"status": "na", "evidence": [], "reason": "测试样例不适用"}
        for name in MODULE.REQUIRED_CHECKS
    }
    checks["cross_output_consistency"] = {
        "status": "pass",
        "evidence": ["results/result_registry.json"],
        "reason": "",
    }
    checks["data_integrity_and_leakage"] = {
        "status": "pass",
        "evidence": ["results/data_audit.json"],
        "reason": "",
    }
    checks["objective_metric_alignment"] = {
        "status": "pass",
        "evidence": ["results/objective_map.json"],
        "reason": "",
    }
    checks["implementation_and_oracle"] = {
        "status": "pass",
        "evidence": ["results/oracle.json"],
        "reason": "",
        "oracle_audit": {
            "coverage_statement": "覆盖测试夹具中的唯一核心计算例程",
            "oracles": [{
                "id": "O1",
                "type": "hand_calculation",
                "target": "测试夹具约束计算",
                "discriminating_case": "手算边界反例",
                "expected": "约束余量为零",
                "observed": "约束余量为零",
                "status": "pass",
                "evidence": ["results/oracle.json"],
            }],
        },
    }
    return {
        "schema_version": "1.3",
        "project": "测试项目",
        "status": "pass",
        "applicable_checks": checks,
        "parameter_audit": {
            "status": "na",
            "reason": "测试样例没有估计、校准、阈值或决策参数",
        },
        "claims": [
            {
                "id": "C1",
                "text": "方案可行",
                "strength": "feasible",
                "status": "pass",
                "evidence": ["results/constraints.json"],
                "search_scope": "当前方案",
                "falsification": "任一硬约束失败",
                "proof": "",
            }
        ],
        "findings": [],
    }


class RedTeamGateTests(unittest.TestCase):
    def test_non_object_top_level_fails_cleanly(self):
        self.assertEqual(MODULE.validate([]), ["red_team_audit.json 顶层必须为对象"])

    def test_passing_audit(self):
        self.assertEqual(MODULE.validate(passing_audit()), [])

    def test_pass_without_evidence_fails(self):
        data = passing_audit()
        data["applicable_checks"]["grid_convergence"] = {
            "status": "pass",
            "evidence": [],
            "reason": "",
        }
        self.assertTrue(any("没有证据" in error for error in MODULE.validate(data)))

    def test_global_claim_requires_proof(self):
        data = passing_audit()
        data["claims"][0]["strength"] = "global"
        self.assertTrue(any("严格证明" in error for error in MODULE.validate(data)))

    def test_old_schema_and_missing_parameter_audit_fail(self):
        data = passing_audit()
        data["schema_version"] = "1.0"
        del data["parameter_audit"]
        errors = MODULE.validate(data)
        self.assertTrue(any("1.3" in error for error in errors))
        self.assertTrue(any("parameter_audit" in error for error in errors))

    def test_always_applicable_checks_cannot_be_marked_na(self):
        data = passing_audit()
        data["applicable_checks"]["implementation_and_oracle"] = {
            "status": "na",
            "evidence": [],
            "reason": "没有做独立核验",
        }
        errors = MODULE.validate(data)
        self.assertTrue(any("不得标为 na" in error for error in errors))

    def test_implementation_check_requires_discriminating_oracle(self):
        data = passing_audit()
        del data["applicable_checks"]["implementation_and_oracle"]["oracle_audit"]
        errors = MODULE.validate(data)
        self.assertTrue(any("缺少 oracle_audit" in error for error in errors))

        data = passing_audit()
        oracle = data["applicable_checks"]["implementation_and_oracle"]["oracle_audit"]["oracles"][0]
        oracle["discriminating_case"] = ""
        errors = MODULE.validate(data)
        self.assertTrue(any("discriminating_case" in error for error in errors))

    def test_boundary_risk_requires_constraint_refit(self):
        data = passing_audit()
        data["applicable_checks"]["parameter_semantics_and_constraints"] = {
            "status": "pass",
            "evidence": ["results/parameter_audit.csv"],
            "reason": "",
        }
        data["parameter_audit"] = {
            "status": "pass",
            "coverage_statement": "覆盖全部影响核心结论的参数",
            "parameters": [
                {
                    "name": "theta",
                    "role": "统计参数",
                    "unit": "无量纲",
                    "expected_domain": "theta >= 0",
                    "domain_basis": "模型定义",
                    "observed_value_or_range": "-0.1",
                    "observed_behavior": "无约束估计越界",
                    "status": "pass",
                    "boundary_risk": True,
                    "evidence": ["results/parameter_audit.csv"],
                }
            ],
            "cross_model_comparison": {
                "status": "na",
                "evidence": [],
                "reason": "没有独立模型路线",
            },
        }
        self.assertTrue(any("constraint_refit" in error for error in MODULE.validate(data)))

    def test_boundary_risk_with_constraint_refit_passes(self):
        data = passing_audit()
        data["applicable_checks"]["parameter_semantics_and_constraints"] = {
            "status": "pass",
            "evidence": ["results/parameter_audit.csv", "results/constrained_refit.csv"],
            "reason": "",
        }
        data["parameter_audit"] = {
            "status": "pass",
            "coverage_statement": "覆盖全部影响核心结论的参数",
            "parameters": [
                {
                    "name": "theta",
                    "role": "统计参数",
                    "unit": "无量纲",
                    "expected_domain": "theta >= 0",
                    "domain_basis": "模型定义",
                    "observed_value_or_range": "无约束-0.1，约束后0",
                    "observed_behavior": "无约束越界，约束后满足定义域",
                    "status": "pass",
                    "boundary_risk": True,
                    "evidence": ["results/parameter_audit.csv"],
                    "constraint_refit": {
                        "unrestricted_result": "核心输出=2.0",
                        "constrained_result": "核心输出=1.8",
                        "impact_on_core_outputs": "核心输出下降10%，主张已修订",
                        "conclusion": "采用约束模型",
                        "evidence": ["results/constrained_refit.csv"],
                    },
                }
            ],
            "cross_model_comparison": {
                "status": "na",
                "evidence": [],
                "reason": "没有独立模型路线",
            },
        }
        self.assertEqual(MODULE.validate(data), [])

    def test_parameter_check_and_audit_status_must_match(self):
        data = passing_audit()
        data["applicable_checks"]["parameter_semantics_and_constraints"] = {
            "status": "pass",
            "evidence": ["results/parameter_audit.csv"],
            "reason": "",
        }
        self.assertTrue(any("状态不一致" in error for error in MODULE.validate(data)))

    def test_evidence_files_are_checked_when_project_root_is_supplied(self):
        data = passing_audit()
        with tempfile.TemporaryDirectory() as tmp:
            errors = MODULE.validate(data, project_root=Path(tmp))
        self.assertTrue(any("证据文件不存在" in error for error in errors))

    def test_finding_severity_typos_cannot_bypass_gate(self):
        data = passing_audit()
        data["findings"] = [{"id": "F1", "severity": "majro", "status": "closed"}]
        errors = MODULE.validate(data)
        self.assertTrue(any("严重度无效" in error for error in errors))

    def test_closed_major_finding_requires_resolution_evidence(self):
        data = passing_audit()
        data["findings"] = [{"id": "F1", "severity": "major", "status": "closed"}]
        errors = MODULE.validate(data)
        self.assertTrue(any("resolution" in error for error in errors))
        self.assertTrue(any("修复证据" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
