import importlib.util
import sys
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
    return {
        "schema_version": "1.0",
        "project": "测试项目",
        "status": "pass",
        "applicable_checks": checks,
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


if __name__ == "__main__":
    unittest.main()
