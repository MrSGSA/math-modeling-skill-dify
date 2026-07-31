import ast
import heapq
import importlib.util
import itertools
import re
import unittest
from pathlib import Path

import numpy as np
from scipy import stats


ROOT = Path(__file__).resolve().parents[1]
ALGORITHMS = ROOT / "references" / "algorithms"


def python_block_after(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    section = text.index(heading)
    start = text.index("```python", section) + len("```python")
    end = text.index("```", start)
    return text[start:end]


class AlgorithmReferenceTests(unittest.TestCase):
    def test_all_markdown_python_blocks_compile(self):
        failures = []
        for path in ROOT.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            for index, match in enumerate(re.finditer(r"```python\s*\n(.*?)```", text, re.DOTALL), 1):
                try:
                    compile(match.group(1), f"{path.name}#{index}", "exec")
                except SyntaxError as exc:
                    failures.append(f"{path.relative_to(ROOT)}#{index}:{exc.lineno} {exc.msg}")
        self.assertEqual(failures, [])

    def test_dea_reference_has_valid_input_and_output_orientation(self):
        code = python_block_after(ALGORITHMS / "03-评价类算法说明.md", "## 9. 数据包络分析")
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "dea-reference", "exec"), namespace)
        inputs = np.array([[1.0], [2.0]])
        outputs = np.array([[1.0], [1.0]])
        input_eff = namespace["dea_ccr"](inputs, outputs, "input")
        output_eff = namespace["dea_ccr"](inputs, outputs, "output")
        np.testing.assert_allclose(input_eff, [1.0, 0.5], atol=1e-8)
        np.testing.assert_allclose(output_eff, [1.0, 0.5], atol=1e-8)
        bcc_input, scale = namespace["dea_bcc"](inputs, outputs, "input")
        self.assertTrue(np.all(np.isfinite(bcc_input)))
        self.assertTrue(np.all((scale >= 0) & (scale <= 1 + 1e-8)))

    def test_ahp_reference_rejects_incomplete_or_unsupported_inputs(self):
        code = python_block_after(
            ALGORITHMS / "03-评价类算法说明.md",
            "## 1. 层次分析法",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "ahp-reference", "exec"), namespace)
        analyzer = namespace["AHPAnalyzer"]
        weights = np.array([0.5, 0.3, 0.2])
        matrix = weights[:, None] / weights[None, :]
        estimated, _, _, cr, passed = analyzer.calculate_weights_eigenvalue(
            matrix, consistency_threshold=0.1
        )
        np.testing.assert_allclose(estimated, weights)
        self.assertAlmostEqual(cr, 0.0, places=10)
        self.assertTrue(passed)
        with self.assertRaises(ValueError):
            analyzer.create_comparison_matrix(
                ["A", "B", "C"], {(0, 1): 2.0}
            )
        with self.assertRaises(ValueError):
            analyzer.calculate_weights_eigenvalue(np.ones((11, 11)))
        total = analyzer.comprehensive_evaluation(
            [0.5, 0.5],
            [[-10.0, 10.0], [-5.0, 1.0]],
            directions=[1, -1],
        )
        np.testing.assert_allclose(total, [0.0, 1.0])
        with self.assertRaises(ValueError):
            analyzer.comprehensive_evaluation(
                [0.5, 0.5], [[1.0, 2.0], [2.0, 1.0]], directions=[1]
            )
        self.assertIn("未判定（未提供阈值）", code)

    def test_entropy_weight_reference_handles_zero_probabilities_exactly(self):
        code = python_block_after(
            ALGORITHMS / "03-评价类算法说明.md",
            "## 3. 熵权法",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "entropy-weight-reference", "exec"), namespace)
        method = namespace["EntropyWeightMethod"]
        weights, entropy, utility, _ = method.calculate_weights(
            np.array([[1.0, 5.0], [2.0, 5.0], [4.0, 5.0]])
        )
        np.testing.assert_allclose(weights, [1.0, 0.0], atol=1e-12)
        self.assertAlmostEqual(entropy[1], 1.0)
        self.assertAlmostEqual(utility[1], 0.0)
        with self.assertRaises(ValueError):
            method.calculate_weights(np.ones((3, 2)))
        self.assertNotIn("0.0001", code)
        full_text = (ALGORITHMS / "03-评价类算法说明.md").read_text(encoding="utf-8")
        self.assertNotIn("通常进行坐标平移", full_text)
        self.assertIn("0\\ln 0=0", full_text)

    def test_topsis_reference_handles_degenerate_ties_without_epsilon(self):
        code = python_block_after(
            ALGORITHMS / "03-评价类算法说明.md",
            "## 4. 优劣解距离法",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "topsis-reference", "exec"), namespace)
        analyzer = namespace["TOPSISAnalyzer"]
        closeness, *_ = analyzer.evaluate(
            np.ones((3, 2)), weights=[2.0, 1.0], directions=[1, -1]
        )
        np.testing.assert_allclose(closeness, [0.5, 0.5, 0.5])
        with self.assertRaises(ValueError):
            analyzer.evaluate(np.ones((2, 2)), weights=[1.0, -1.0])
        self.assertNotIn("d_positive + d_negative + 1e-10", code)

    def test_improved_topsis_reference_preserves_method_semantics(self):
        code = python_block_after(
            ALGORITHMS / "03-评价类算法说明.md",
            "## 11. 改进的TOPSIS方法",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "improved-topsis-reference", "exec"), namespace)

        data_3d = np.ones((3, 2, 2))
        with self.assertRaises(TypeError):
            namespace["dynamic_topsis"](data_3d, weights=[1.0, 1.0])
        period, overall = namespace["dynamic_topsis"](
            data_3d,
            weights=[1.0, 1.0],
            period_weights=[0.25, 0.75],
        )
        np.testing.assert_allclose(period, 0.5)
        np.testing.assert_allclose(overall, 0.5)

        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            scores = namespace["prospect_value_score"](
                data=np.array([[1.0], [-1.0]]),
                weights=[1.0],
                reference=[0.0],
                directions=[1],
                alpha=0.5,
                beta=0.5,
                loss_aversion=2.25,
            )
        self.assertGreater(scores[0], scores[1])
        self.assertEqual(caught, [])

        self.assertNotIn("1e-8", code)
        self.assertNotIn("gamma", code)
        self.assertNotIn("prospect_topsis", namespace)

    def test_stochastic_optimizers_are_seeded_runnable_and_deterministic(self):
        path = ALGORITHMS / "01-优化算法说明.md"
        sphere = lambda x: float(np.dot(x, x))

        ga_code = python_block_after(path, "## 4. 遗传算法")
        ga_ns = {"__name__": "reference_test"}
        exec(compile(ga_code, "ga-reference", "exec"), ga_ns)
        ga_kwargs = dict(pop_size=8, n_generations=3, elitism=2,
                         bounds=(-2.0, 2.0), seed=2026)
        first_ga = ga_ns["GeneticAlgorithm"](**ga_kwargs).evolve(dim=2, verbose=False)[:2]
        second_ga = ga_ns["GeneticAlgorithm"](**ga_kwargs).evolve(dim=2, verbose=False)[:2]
        np.testing.assert_allclose(first_ga[0], second_ga[0])
        self.assertEqual(first_ga[1], second_ga[1])

        pso_code = python_block_after(path, "## 5. 粒子群优化算法")
        pso_ns = {"__name__": "reference_test"}
        exec(compile(pso_code, "pso-reference", "exec"), pso_ns)
        pso_kwargs = dict(n_particles=6, n_iterations=4, bounds=(-2.0, 2.0), seed=2026)
        first_pso = pso_ns["ParticleSwarmOptimization"](**pso_kwargs).optimize(
            dim=2, verbose=False
        )
        second_pso = pso_ns["ParticleSwarmOptimization"](**pso_kwargs).optimize(
            dim=2, verbose=False
        )
        np.testing.assert_allclose(first_pso[0], second_pso[0])
        self.assertEqual(first_pso[1], second_pso[1])

        cases = [
            ("## 10. 灰狼优化算法", "grey_wolf_optimizer",
             dict(n_wolves=6, max_iter=4, seed=2026)),
            ("## 11. 免疫算法", "clonal_selection_optimizer",
             dict(n_antibodies=6, clone_factor=1, mutation_scale=0.05,
                  immigrant_fraction=0.2, max_iter=3, seed=2026)),
            ("## 12. 鲸鱼优化算法", "whale_optimization",
             dict(n_whales=6, max_iter=4, seed=2026)),
            ("## 13. 麻雀搜索算法", "sparrow_search_algorithm",
             dict(n_sparrows=8, max_iter=4, ST=0.8,
                  producer_fraction=0.25, warning_fraction=0.25, seed=2026)),
        ]
        for heading, function_name, kwargs in cases:
            with self.subTest(algorithm=function_name):
                code = python_block_after(path, heading)
                namespace = {"__name__": "reference_test"}
                exec(compile(code, f"{function_name}-reference", "exec"), namespace)
                first = namespace[function_name](sphere, 2, (-2.0, 2.0), **kwargs)
                second = namespace[function_name](sphere, 2, (-2.0, 2.0), **kwargs)
                np.testing.assert_allclose(first[0], second[0])
                self.assertEqual(first[1], second[1])
                self.assertTrue(np.all(np.diff(first[2]) <= 0))

    def test_linear_programming_reference_fails_loudly_and_shades_actual_constraints(self):
        code = python_block_after(
            ALGORITHMS / "01-优化算法说明.md",
            "## 1. 线性规划",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "lp-reference", "exec"), namespace)
        solution = namespace["solve_linear_programming"]()
        np.testing.assert_allclose(solution["decision"], [3.0, 4.0], atol=1e-8)
        self.assertAlmostEqual(solution["objective_value"], 11.0, places=8)
        np.testing.assert_allclose(solution["constraint_slacks"], [0.0, 0.0], atol=1e-8)
        self.assertIn("points @ A_ub.T <= b_ub", code)
        self.assertNotIn("x2_const1 = np.maximum", code)
        self.assertNotIn("单纯形法搜索路径", code)
        self.assertNotIn("return None", code)

        class FailedResult:
            success = False
            x = None
            message = "infeasible"

        namespace["linprog"] = lambda *args, **kwargs: FailedResult()
        with self.assertRaises(RuntimeError):
            namespace["solve_linear_programming"]()

    def test_tsp_metaheuristics_return_closed_tour_evidence(self):
        path = ALGORITHMS / "01-优化算法说明.md"
        distances = np.array([
            [0.0, 1.0, 3.0, 2.0],
            [1.0, 0.0, 1.0, 3.0],
            [3.0, 1.0, 0.0, 1.0],
            [2.0, 3.0, 1.0, 0.0],
        ])
        sa_code = python_block_after(path, "## 6. 模拟退火算法")
        sa_ns = {"__name__": "reference_test"}
        exec(compile(sa_code, "sa-reference", "exec"), sa_ns)
        sa = sa_ns["simulated_annealing_tsp"](
            distances, initial_temperature=2.0, cooling_rate=0.99,
            n_iterations=200, seed=2026,
        )
        self.assertEqual(set(sa[0].tolist()), set(range(4)))
        self.assertAlmostEqual(sa[1], distances[sa[0], np.roll(sa[0], -1)].sum())

        aco_code = python_block_after(path, "## 7. 蚁群算法")
        aco_ns = {"__name__": "reference_test"}
        exec(compile(aco_code, "aco-reference", "exec"), aco_ns)
        kwargs = dict(n_ants=8, n_iterations=8, alpha=1.0, beta=2.0,
                      evaporation_rate=0.2, seed=2026)
        first = aco_ns["ant_colony_tsp"](distances, **kwargs)
        second = aco_ns["ant_colony_tsp"](distances, **kwargs)
        np.testing.assert_array_equal(first[0], second[0])
        self.assertEqual(first[1], second[1])
        self.assertEqual(set(first[0].tolist()), set(range(4)))
        self.assertAlmostEqual(first[1], distances[first[0], np.roll(first[0], -1)].sum())

    def test_finite_scenario_robust_reference_reports_coverage(self):
        code = python_block_after(
            ALGORITHMS / "01-优化算法说明.md",
            "## 15. 鲁棒优化",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "robust-reference", "exec"), namespace)
        result, audit = namespace["finite_scenario_robust_optimization"](
            objective_func=lambda x, scenario: (x[0] - scenario[0]) ** 2,
            constraint_func=lambda x, scenario: np.array([x[0]]),
            scenarios=np.array([[0.0], [2.0]]),
            x0=np.array([0.5]),
            bounds=[(0.0, 2.0)],
        )
        self.assertTrue(result.success)
        self.assertAlmostEqual(audit["decision"][0], 1.0, places=5)
        self.assertAlmostEqual(audit["epigraph_value"], 1.0, places=5)
        self.assertEqual(audit["coverage"], "finite_scenarios_only")

    def test_evaluation_references_handle_degenerate_inputs_explicitly(self):
        path = ALGORITHMS / "03-评价类算法说明.md"
        gra_code = python_block_after(path, "## 5. 灰色关联分析")
        gra_ns = {"__name__": "reference_test"}
        exec(compile(gra_code, "gra-reference", "exec"), gra_ns)
        grades, coefficients = gra_ns["grey_relational_analysis"](np.ones((3, 2)))
        np.testing.assert_allclose(grades, 1.0)
        np.testing.assert_allclose(coefficients, 1.0)
        mixed_grades, _ = gra_ns["grey_relational_analysis"](
            np.array([[10.0, 1.0], [5.0, 2.0]]),
            directions=[1, -1],
        )
        self.assertGreater(mixed_grades[0], mixed_grades[1])

        cv_code = python_block_after(path, "## 7. 变异系数法")
        cv_ns = {"__name__": "reference_test"}
        exec(compile(cv_code, "cv-reference", "exec"), cv_ns)
        weights, cvs = cv_ns["coefficient_of_variation_weight"](
            np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0]])
        )
        np.testing.assert_allclose(weights, [1.0, 0.0])
        self.assertTrue(np.all(np.isfinite(cvs)))
        with self.assertRaises(ValueError):
            cv_ns["coefficient_of_variation_weight"]([[-1.0], [1.0]])
        with self.assertRaises(ValueError):
            cv_ns["coefficient_of_variation_weight"]([[1.0, 2.0], [1.0, 2.0]])
        directed_scores, _ = cv_ns["cv_evaluation"](
            [[1.0, 10.0], [2.0, 5.0], [3.0, 1.0]], directions=[1, -1]
        )
        self.assertGreater(directed_scores[2], directed_scores[0])
        with self.assertRaises(ValueError):
            cv_ns["cv_evaluation"](
                [[1.0, 10.0], [2.0, 5.0]], directions=[1]
            )

        rsr_code = python_block_after(path, "## 6. 秩和比法")
        rsr_ns = {"__name__": "reference_test"}
        exec(compile(rsr_code, "rsr-reference", "exec"), rsr_ns)
        labels = rsr_ns["rsr_classification"](
            np.array([0.2, 0.5, 0.8]), [0.3, 0.7], ["低", "中", "高"]
        )
        self.assertEqual(labels.tolist(), ["低", "中", "高"])
        with self.assertRaises(TypeError):
            rsr_ns["rsr_classification"](np.array([0.5]))

    def test_cellular_automata_are_reproducible_and_collision_free(self):
        code = python_block_after(
            ALGORITHMS / "06-综合类算法说明.md",
            "## 4. 元胞自动机",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "ca-reference", "exec"), namespace)
        first_life = namespace["game_of_life"](8, 9, 5, 0.3, seed=2026)
        second_life = namespace["game_of_life"](8, 9, 5, 0.3, seed=2026)
        np.testing.assert_array_equal(first_life, second_life)
        traffic = namespace["traffic_ca"](
            n_cells=20, n_steps=12, density=0.4,
            v_max=4, p_slow=0.2, seed=2026,
        )
        self.assertEqual(traffic.shape, (12, 8))
        for state in traffic:
            self.assertEqual(len(np.unique(state)), len(state))
            self.assertTrue(np.all(np.diff(state) >= 0))

    def test_zero_sum_game_uses_both_primal_and_dual_programs(self):
        code = python_block_after(
            ALGORITHMS / "06-综合类算法说明.md",
            "## 3. 博弈论",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "game-theory-reference", "exec"), namespace)
        matching_pennies = np.array([[1.0, -1.0], [-1.0, 1.0]])
        result = namespace["solve_zero_sum_game"](matching_pennies)
        np.testing.assert_allclose(result["row_strategy"], [0.5, 0.5], atol=1e-8)
        np.testing.assert_allclose(result["column_strategy"], [0.5, 0.5], atol=1e-8)
        self.assertAlmostEqual(result["row_value"], 0.0, places=8)
        self.assertAlmostEqual(result["column_value"], 0.0, places=8)
        self.assertAlmostEqual(result["duality_gap"], 0.0, places=8)
        self.assertNotIn("plt.plot(p, p", code)
        self.assertNotIn("return None, None", code)

    def test_ode_reference_stops_exactly_at_requested_endpoint(self):
        code = python_block_after(
            ALGORITHMS / "06-综合类算法说明.md",
            "## 6. 微分方程建模",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "ode-reference", "exec"), namespace)
        derivative = lambda t, y: np.ones_like(y)
        for solver_name in ("solve_ode_euler", "solve_ode_rk4"):
            with self.subTest(solver=solver_name):
                t, y = namespace[solver_name](derivative, [0.0], (0.0, 1.0), h=0.3)
                self.assertEqual(t[-1], 1.0)
                self.assertTrue(np.all(t <= 1.0))
                self.assertAlmostEqual(y[-1, 0], 1.0, places=12)

    def test_pca_and_nmf_reference_preserve_fitted_state_and_factor_semantics(self):
        path = ALGORITHMS / "05-统计分析与数据处理算法说明.md"
        pca_code = python_block_after(path, "## 4. 主成分分析")
        nmf_code = python_block_after(path, "## 7. 非负矩阵分解")
        self.assertIn("return pca, transformed_data, scaler", pca_code)
        self.assertIn("basis = H[i].reshape(image_shape)", nmf_code)
        self.assertNotIn("basis = W[:, i].reshape(image_shape)", nmf_code)

        if importlib.util.find_spec("sklearn") is None:
            self.skipTest("scikit-learn 未安装；已完成不依赖可选包的接口与因子语义检查")

        pca_ns = {"__name__": "reference_test"}
        exec(compile(pca_code, "pca-reference", "exec"), pca_ns)
        data = np.array([
            [1.0, 2.0, 1.0],
            [2.0, 3.0, 1.5],
            [3.0, 5.0, 2.0],
            [4.0, 7.0, 2.5],
        ])
        pca, transformed, scaler = pca_ns["perform_pca"](
            data, n_components=2, standardize=True
        )
        self.assertEqual(transformed.shape, (4, 2))
        self.assertIsNotNone(scaler)
        count = pca_ns["components_for_variance_target"](pca, 0.8)
        self.assertGreaterEqual(count, 1)
        self.assertLessEqual(count, 2)
        with self.assertRaises(ValueError):
            pca_ns["components_for_variance_target"](pca, 0.0)

        nmf_ns = {"__name__": "reference_test"}
        exec(compile(nmf_code, "nmf-reference", "exec"), nmf_ns)
        W, H, error = nmf_ns["nmf_decomposition"](
            np.abs(data), n_components=2, max_iter=500, random_state=2026
        )
        self.assertEqual(W.shape, (4, 2))
        self.assertEqual(H.shape, (2, 3))
        self.assertGreaterEqual(error, 0.0)
        with self.assertRaises(ValueError):
            nmf_ns["nmf_decomposition"]([[-1.0, 2.0]], n_components=1)

    def test_algorithm_references_have_no_silent_placeholders_or_global_rng(self):
        violations = []
        patterns = {
            "legacy global RNG": re.compile(r"np\.random\.(?!default_rng)"),
            "bare except": re.compile(r"(?m)^\s*except\s*:"),
            "pass placeholder": re.compile(r"(?m)^\s*pass\s*$"),
            "arbitrary additive epsilon": re.compile(r"\+\s*(?:1e-\d+|0\.0001)"),
        }
        for path in ALGORITHMS.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            for label, pattern in patterns.items():
                if pattern.search(text):
                    violations.append(f"{path.name}: {label}")
        self.assertEqual(violations, [])

    def test_gm_reference_uses_stable_fit_and_rejects_invalid_domain(self):
        code = python_block_after(ALGORITHMS / "02-预测类算法说明.md", "## 1. 灰色预测模型")
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "gm-reference", "exec"), namespace)
        model = namespace["GreyPredictionModel"]([1.0, 1.2, 1.44, 1.728]).fit()
        self.assertTrue(np.all(np.isfinite(model.predict(6))))
        model.a = 1e-14
        model.b = 2.0
        self.assertTrue(np.all(np.isfinite(model.predict(6))))
        np.testing.assert_allclose(model.predict(6)[1:], 2.0, rtol=1e-12, atol=1e-12)
        with self.assertRaises(ValueError):
            namespace["GreyPredictionModel"]([1.0, 0.0, 1.2, 1.3])
        self.assertIn("np.expm1(self.a) / self.a", code)
        self.assertNotIn("1e-10", code)

    def test_interpolation_reference_enforces_node_and_degree_contracts(self):
        code = python_block_after(
            ALGORITHMS / "02-预测类算法说明.md",
            "## 2. 插值与拟合",
        )
        parsed = ast.parse(code)
        class_node = next(
            node for node in parsed.body
            if isinstance(node, ast.ClassDef) and node.name == "InterpolationFitting"
        )
        namespace = {"np": np}
        exec(
            compile(ast.Module(body=[class_node], type_ignores=[]),
                    "interpolation-contract-reference", "exec"),
            namespace,
        )
        methods = namespace["InterpolationFitting"]
        scalar = methods.lagrange_interpolation([0.0, 1.0], [0.0, 2.0], 0.25)
        self.assertEqual(scalar.shape, ())
        self.assertAlmostEqual(float(scalar), 0.5)
        for function_name in (
            "lagrange_interpolation", "newton_interpolation", "cubic_spline_interpolation"
        ):
            with self.subTest(function=function_name):
                with self.assertRaises(ValueError):
                    getattr(methods, function_name)([0.0, 0.0], [1.0, 2.0], [0.5])
        with self.assertRaises(ValueError):
            methods.polynomial_fit([0.0, 1.0, 2.0], [1.0, 2.0, 3.0], degree=3)
        self.assertIn("返回指标只描述训练内拟合", code)

    def test_prediction_references_separate_training_validation_and_final_scoring(self):
        path = ALGORITHMS / "02-预测类算法说明.md"
        regression = python_block_after(path, "## 3. 线性回归")
        neural = python_block_after(path, "## 4. 神经网络")
        arima = python_block_after(path, "## 6. ARIMA模型")
        smoothing = python_block_after(path, "## 7. 指数平滑法")
        spatiotemporal = python_block_after(path, "## 10. 时空预测模型")

        self.assertIn("'training_r2':", regression)
        self.assertIn("'metric_scope': 'in_sample_fit_only'", regression)
        self.assertIn("def evaluate_regression_predictions", regression)

        self.assertNotIn("validation_split=", neural)
        self.assertIn("validation_data=(X_validation, y_validation)", neural)
        self.assertIn("keras.utils.set_random_seed(random_state)", neural)
        self.assertIn("lstm_units < 2", neural)
        self.assertIn("最终测试集只能在模型冻结后评分一次", neural)

        self.assertIn('"selected_order": selected_order', arima)
        self.assertIn('"selection_scope": "declared_candidate_grid_aic_only"', arima)
        self.assertNotIn('"best_order"', arima)

        self.assertIn("def simple_exponential_smoothing(data, alpha, forecast_steps)", smoothing)
        self.assertIn("def holt_linear_trend(data, alpha, beta, forecast_steps)", smoothing)
        self.assertIn("def holt_winters(data, seasonal_periods, forecast_steps", smoothing)
        self.assertNotIn("seasonal_periods=12", smoothing)
        self.assertNotIn("forecast(steps=5)", smoothing)

        self.assertIn("class SimplifiedGraphTemporalPredictor", spatiotemporal)
        self.assertNotIn("class STGCNPredictor", spatiotemporal)
        self.assertIn("X_validation, y_validation", spatiotemporal)
        self.assertIn("best_epoch_by_validation_loss", spatiotemporal)
        self.assertIn("torch.manual_seed(random_state)", spatiotemporal)
        self.assertIn("not_original_stgcn", spatiotemporal)

    def test_hypothesis_reference_uses_matching_welch_interval(self):
        code = python_block_after(
            ALGORITHMS / "05-统计分析与数据处理算法说明.md",
            "## 3. 假设检验",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "hypothesis-reference", "exec"), namespace)
        first = np.array([1.0, 2.0, 3.0, 4.0])
        second = np.array([8.0, 11.0, 14.0])
        result = namespace["two_sample_t_test"](
            first, second, alpha=0.05, equal_var=False
        )
        term1 = np.var(first, ddof=1) / len(first)
        term2 = np.var(second, ddof=1) / len(second)
        expected_df = (term1 + term2) ** 2 / (
            term1**2 / (len(first) - 1) + term2**2 / (len(second) - 1)
        )
        self.assertAlmostEqual(result["degrees_of_freedom"], expected_df)
        self.assertFalse(result["equal_variance_assumed"])
        normality = namespace["normality_test"](
            np.linspace(-1.0, 1.0, 20), alpha=0.05
        )
        self.assertIn("normality_not_rejected", normality)
        self.assertNotIn("is_normal", normality)

    def test_preprocessing_reference_records_power_transform_shift(self):
        code = python_block_after(
            ALGORITHMS / "05-统计分析与数据处理算法说明.md",
            "## 1. 数据预处理",
        )
        parsed = ast.parse(code)
        class_node = next(
            node for node in parsed.body
            if isinstance(node, ast.ClassDef) and node.name == "DataPreprocessor"
        )
        isolated = ast.Module(body=[class_node], type_ignores=[])
        namespace = {"np": np, "stats": stats}
        exec(compile(isolated, "preprocessing-reference", "exec"), namespace)
        processor = namespace["DataPreprocessor"]
        transformed, fitted_lambda, shift = processor.boxcox_transform(
            np.array([1.0, 2.0, 3.0]), lmbda=0.0
        )
        np.testing.assert_allclose(transformed, np.log([1.0, 2.0, 3.0]))
        self.assertEqual(fitted_lambda, 0.0)
        self.assertEqual(shift, 0.0)
        with self.assertRaises(ValueError):
            processor.boxcox_transform(np.array([-1.0, 2.0]))
        _, _, recorded_shift = processor.boxcox_transform(
            np.array([-1.0, 2.0]), shift=2.0
        )
        self.assertEqual(recorded_shift, 2.0)
        self.assertIn("df_filled, missing_stats, imputer", code)
        self.assertIn("threshold=3.0", code)
        self.assertIn("k=1.5", code)
        self.assertIn("transformed, lmbda, applied_shift", code)

    def test_clustering_reference_reports_candidates_without_fake_optimality(self):
        code = python_block_after(
            ALGORITHMS / "05-统计分析与数据处理算法说明.md",
            "## 2. 聚类分析",
        )
        self.assertIn("def kmeans_inertia_curve(data, candidate_ks", code)
        self.assertNotIn("def find_optimal_k", code)
        self.assertIn("def gmm_information_criterion_curve", code)
        self.assertNotIn("def find_optimal_gmm_components", code)
        self.assertIn("'selection_scope': 'declared_candidates_only'", code)
        self.assertIn("elif gmm.covariance_type == 'tied'", code)
        self.assertIn("chi2.ppf(level, df=2)", code)
        self.assertIn("Model-relative max assignment posterior", code)
        self.assertIn("num_iterations", code)
        self.assertNotIn("return None, None, None", code)

        parsed = ast.parse(code)
        function = next(
            node for node in parsed.body
            if isinstance(node, ast.FunctionDef) and node.name == "kmeans_inertia_curve"
        )

        class FakeKMeans:
            def __init__(self, n_clusters, random_state, n_init):
                self.n_clusters = n_clusters

            def fit(self, data):
                self.inertia_ = float(self.n_clusters)
                return self

        namespace = {"np": np, "KMeans": FakeKMeans}
        exec(
            compile(ast.Module(body=[function], type_ignores=[]),
                    "kmeans-candidate-reference", "exec"),
            namespace,
        )
        result = namespace["kmeans_inertia_curve"](
            np.ones((3, 2)), [1, 3], plot=False
        )
        self.assertEqual(result, {"candidate_ks": [1, 3], "inertia": [1.0, 3.0]})
        with self.assertRaises(ValueError):
            namespace["kmeans_inertia_curve"](
                np.ones((3, 2)), [1, 4], plot=False
            )

    def test_factor_count_reference_labels_kaiser_as_a_capped_heuristic(self):
        code = python_block_after(
            ALGORITHMS / "05-统计分析与数据处理算法说明.md",
            "## 5. 因子分析",
        )
        parsed = ast.parse(code)
        function = next(
            node for node in parsed.body
            if isinstance(node, ast.FunctionDef) and node.name == "factor_count_diagnostics"
        )
        namespace = {"np": np}
        exec(
            compile(ast.Module(body=[function], type_ignores=[]),
                    "factor-count-reference", "exec"),
            namespace,
        )
        data = np.array([
            [1.0, 1.1, 5.0],
            [2.0, 2.1, 4.0],
            [3.0, 3.2, 2.0],
            [4.0, 4.1, 1.0],
        ])
        result = namespace["factor_count_diagnostics"](data, max_candidates=1)
        self.assertEqual(result["candidate_range"], [1])
        self.assertLessEqual(result["kaiser_count_within_cap"], 1)
        self.assertEqual(result["criterion_status"], "heuristic_only")
        self.assertIn("不等于已确定的最优因子数", result["selection_note"])

    def test_cca_reference_handles_unequal_variable_counts(self):
        code = python_block_after(
            ALGORITHMS / "05-统计分析与数据处理算法说明.md",
            "## 6. 典型相关分析",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "cca-reference", "exec"), namespace)
        rng = np.random.default_rng(2026)
        latent = rng.normal(size=400)
        x = np.column_stack([
            latent + rng.normal(scale=0.1, size=400),
            rng.normal(size=400),
            rng.normal(size=400),
        ])
        y = np.column_stack([
            2 * latent + rng.normal(scale=0.1, size=400),
            rng.normal(size=400),
        ])
        result = namespace["canonical_correlation_analysis"](x, y)
        self.assertEqual(result["coefficients_x"].shape, (3, 2))
        self.assertEqual(result["coefficients_y"].shape, (2, 2))
        self.assertGreater(result["canonical_correlations"][0], 0.9)
        tests = namespace["cca_significance_test"](
            result["canonical_correlations"], 400, 3, 2, alpha=0.05
        )
        self.assertEqual(tests["df"].tolist(), [6, 2])

    def test_ml_reference_does_not_guess_task_from_unique_value_count(self):
        text = (ALGORITHMS / "07-机器学习算法说明.md").read_text(encoding="utf-8")
        self.assertNotIn("len(np.unique(y)) <= 10", text)
        self.assertIn("def plot_oob_score(X, y, task", text)
        self.assertIn("def plot_anomaly_score_distribution", text)
        self.assertNotIn("path_lengths = iso_forest.score_samples", text)

    def test_ml_reference_keeps_validation_selection_separate_from_final_test(self):
        path = ALGORITHMS / "07-机器学习算法说明.md"
        boosting = python_block_after(path, "## 2. AdaBoost")
        isolation = python_block_after(path, "## 3. 孤立森林")

        self.assertIn("def select_adaboost_classifier", boosting)
        self.assertIn("candidate.staged_predict(X_validation)", boosting)
        self.assertNotIn("staged_predict(X_test)", boosting)
        self.assertIn('"selection_scope": "validation_only"', boosting)
        self.assertIn("def evaluate_adaboost_classifier", boosting)
        self.assertIn("模型冻结后对最终测试集调用一次", boosting)

        self.assertIn(
            "def isolation_forest_anomaly_detection(X_fit, X_score, contamination",
            isolation,
        )
        self.assertIn("iso_forest.fit(X_fit)", isolation)
        self.assertIn("iso_forest.predict(X_score)", isolation)
        self.assertIn("X_fit, X_validation", isolation)
        self.assertIn('"selection_scope": "validation_only"', isolation)

        parsed = ast.parse(isolation)
        evaluate_function = next(
            node for node in parsed.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "evaluate_anomaly_detection"
        )
        namespace = {
            "np": np,
            "classification_report": lambda *args, **kwargs: {"ok": True},
            "roc_auc_score": lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("单类别不应调用 ROC AUC")
            ),
        }
        exec(
            compile(ast.Module(body=[evaluate_function], type_ignores=[]),
                    "anomaly-evaluation-reference", "exec"),
            namespace,
        )
        result = namespace["evaluate_anomaly_detection"](
            [1, 1, 1], [1, -1, 1], [0.1, 0.9, 0.2]
        )
        self.assertIsNone(result["roc_auc"])
        self.assertFalse(result["roc_auc_applicable"])
        self.assertIn("无定义", result["roc_auc_note"])

    def test_prediction_reference_does_not_hide_mape_zero_division(self):
        text = (ALGORITHMS / "02-预测类算法说明.md").read_text(encoding="utf-8")
        self.assertNotIn("y_true + 1e-8", text)
        self.assertIn("'MAPE_applicable': mape_applicable", text)
        self.assertIn("summary_frame(", text)
        self.assertIn("def build_spatial_graph(matrix, threshold, mode)", text)
        self.assertNotIn("np.linalg.inv(np.sqrt(D))", text)

    def test_interval_topsis_reports_sensitivity_not_fake_bounds(self):
        code = python_block_after(
            ALGORITHMS / "03-评价类算法说明.md",
            "## 10. 区间数评价",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "interval-topsis-reference", "exec"), namespace)
        data = np.array([
            [[8.0, 9.0], [4.0, 5.0]],
            [[6.0, 8.0], [2.0, 3.0]],
            [[7.0, 7.5], [3.0, 4.0]],
        ])
        result = namespace["interval_topsis"](
            data,
            weights=[0.6, 0.4],
            directions=[1, -1],
        )
        self.assertFalse(result["rigorous_bounds"])
        self.assertEqual(result["method"], "all_box_vertices_plus_midpoint")
        for midpoint, interval in zip(result["midpoint_scores"], result["score_ranges"]):
            self.assertLessEqual(interval.lower, midpoint)
            self.assertGreaterEqual(interval.upper, midpoint)
        point = namespace["IntervalNumber"]
        self.assertEqual(namespace["interval_possibility"](point(3, 3), point(2, 2)), 1.0)
        reflected = point(1, 3) * -2
        self.assertEqual((reflected.lower, reflected.upper), (-6.0, -2.0))

    def test_fuzzy_number_preserves_endpoint_order_for_negative_scalars(self):
        code = python_block_after(
            ALGORITHMS / "03-评价类算法说明.md",
            "## 2. 模糊层次分析法",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "fuzzy-ahp-reference", "exec"), namespace)
        reflected = namespace["FuzzyNumber"](1, 2, 4) * -2
        self.assertEqual((reflected.l, reflected.m, reflected.u), (-8.0, -4.0, -2.0))
        with self.assertRaises(ValueError):
            namespace["FuzzyNumber"](2, 1, 3)

    def test_mahalanobis_topsis_retains_declared_weight_sensitivity(self):
        code = python_block_after(
            ALGORITHMS / "03-评价类算法说明.md",
            "## 11. 改进的TOPSIS方法",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "weighted-mahalanobis-reference", "exec"), namespace)
        data = np.array([
            [10.0, 4.0],
            [8.0, 8.0],
            [5.0, 10.0],
            [2.0, 3.0],
        ])
        first_heavy = namespace["topsis_mahalanobis"](data, [0.9, 0.1])
        second_heavy = namespace["topsis_mahalanobis"](data, [0.1, 0.9])
        self.assertEqual(int(np.argmax(first_heavy)), 0)
        self.assertEqual(int(np.argmax(second_heavy)), 2)
        self.assertFalse(np.allclose(first_heavy, second_heavy))
        self.assertIn("np.cov(normalized, rowvar=False", code)
        self.assertNotIn("np.cov(weighted, rowvar=False", code)

    def test_markov_reference_checks_chain_structure(self):
        code = python_block_after(
            ALGORITHMS / "06-综合类算法说明.md",
            "## 5. 马尔科夫链",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "markov-reference", "exec"), namespace)

        irreducible = np.array([[0.8, 0.2], [0.4, 0.6]])
        np.testing.assert_allclose(
            namespace["find_steady_state"](irreducible),
            [2 / 3, 1 / 3],
            atol=1e-10,
        )
        with self.assertRaises(ValueError):
            namespace["find_steady_state"](np.eye(2))

        absorbing_chain = np.array([
            [0.5, 0.5, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ])
        audit = namespace["calculate_absorption_time"](absorbing_chain)
        self.assertEqual(audit["transient_states"], [0])
        self.assertEqual(audit["absorbing_states"], [1, 2])
        np.testing.assert_allclose(audit["expected_steps"], [2.0])
        np.testing.assert_allclose(audit["absorption_probabilities"], [[1.0, 0.0]])

        mixed_closed_classes = np.array([
            [0.0, 1.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
        ])
        with self.assertRaises(ValueError):
            namespace["calculate_absorption_time"](mixed_closed_classes)

    def test_shortest_path_reference_enforces_weight_contracts(self):
        code = python_block_after(
            ALGORITHMS / "04-图论与网络分析算法说明.md",
            "## 1. 最短路径问题",
        )
        parsed = ast.parse(code)
        class_node = next(
            node for node in parsed.body
            if isinstance(node, ast.ClassDef) and node.name == "ShortestPathAlgorithms"
        )
        namespace = {"np": np, "heapq": heapq, "itertools": itertools}
        exec(
            compile(ast.Module(body=[class_node], type_ignores=[]),
                    "shortest-path-reference", "exec"),
            namespace,
        )
        algorithms = namespace["ShortestPathAlgorithms"]
        graph = {0: {"A": 1.0}, "A": {1: 2.0}, 1: {}}
        distances, previous = algorithms.dijkstra(graph, 0, 1)
        self.assertEqual(distances[1], 3.0)
        self.assertEqual(algorithms.reconstruct_path(previous, 0, 1), [0, "A", 1])
        with self.assertRaises(ValueError):
            algorithms.dijkstra({0: {1: -1.0}, 1: {}}, 0)
        with self.assertRaises(ValueError):
            algorithms.dijkstra({0: {1: 1.0}}, 0)
        negative_cycle = np.array([
            [0.0, -2.0],
            [1.0, 0.0],
        ])
        with self.assertRaises(ValueError):
            algorithms.floyd_warshall(negative_cycle)

    def test_mst_references_are_self_contained_and_reject_disconnected_graphs(self):
        code = python_block_after(
            ALGORITHMS / "04-图论与网络分析算法说明.md",
            "## 2. 最小生成树问题",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "mst-reference", "exec"), namespace)

        # 等权边和异构顶点会让未加计数器的 heap 元组比较失败。
        graph = {
            0: {"A": 1.0, (2,): 1.0},
            "A": {0: 1.0, (2,): 2.0},
            (2,): {0: 1.0, "A": 2.0},
        }
        prim_edges, prim_weight = namespace["prim"](graph)
        self.assertEqual(len(prim_edges), 2)
        self.assertEqual(prim_weight, 2.0)

        source_edges = [(1.0, 0, "A"), (1.0, 0, (2,)), (2.0, "A", (2,))]
        original_order = list(source_edges)
        kruskal_edges, kruskal_weight = namespace["kruskal"](
            source_edges, [0, "A", (2,)]
        )
        self.assertEqual(source_edges, original_order)
        self.assertEqual(len(kruskal_edges), 2)
        self.assertEqual(kruskal_weight, 2.0)

        disconnected = {0: {1: 1.0}, 1: {0: 1.0}, 2: {}}
        with self.assertRaises(ValueError):
            namespace["prim"](disconnected)
        with self.assertRaises(ValueError):
            namespace["kruskal"]([(1.0, 0, 1)], [0, 1, 2])

    def test_cpm_uses_one_project_finish_for_all_terminal_activities(self):
        code = python_block_after(
            ALGORITHMS / "04-图论与网络分析算法说明.md",
            "## 4. 关键路径问题",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "cpm-reference", "exec"), namespace)
        path, params, duration = namespace["critical_path_method"]({
            "long": {"duration": 10.0, "predecessors": []},
            "short": {"duration": 2.0, "predecessors": []},
        })
        self.assertEqual(duration, 10.0)
        self.assertEqual(path, ["long"])
        self.assertTrue(params["long"]["critical"])
        self.assertFalse(params["short"]["critical"])
        self.assertEqual(params["short"]["TF"], 8.0)

    def test_euler_reference_covers_each_undirected_edge_once(self):
        code = python_block_after(
            ALGORITHMS / "04-图论与网络分析算法说明.md",
            "## 5. 欧拉路径与哈密顿路径",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "euler-reference", "exec"), namespace)
        triangle = {0: [1, 2], 1: [0, 2], 2: [0, 1]}
        circuit = namespace["hierholzer_eulerian_circuit"](triangle, 0)
        self.assertEqual(len(circuit), 4)
        traversed = {
            frozenset((left, right)) for left, right in zip(circuit, circuit[1:])
        }
        self.assertEqual(traversed, {frozenset((0, 1)), frozenset((1, 2)), frozenset((0, 2))})
        disconnected_cycles = {
            0: [1, 2], 1: [0, 2], 2: [0, 1],
            3: [4, 5], 4: [3, 5], 5: [3, 4],
        }
        with self.assertRaises(ValueError):
            namespace["hierholzer_eulerian_circuit"](disconnected_cycles, 0)

    def test_hungarian_reference_minimizes_actual_assignment_cost(self):
        code = python_block_after(
            ALGORITHMS / "04-图论与网络分析算法说明.md",
            "## 6. 匹配问题",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "assignment-reference", "exec"), namespace)
        objective, matching = namespace["hungarian_algorithm"](
            [[10.0, 1.0], [1.0, 10.0]]
        )
        self.assertEqual(objective, 2.0)
        self.assertEqual(set(matching), {(0, 1), (1, 0)})

    def test_max_flow_reference_can_cancel_an_earlier_augmentation(self):
        code = python_block_after(
            ALGORITHMS / "04-图论与网络分析算法说明.md",
            "## 3. 网络流问题",
        )
        parsed = ast.parse(code)
        selected = [
            node for node in parsed.body
            if isinstance(node, ast.ClassDef) and node.name in {"Edge", "MaxFlow"}
        ]
        from collections import deque
        namespace = {"np": np, "deque": deque}
        exec(
            compile(ast.Module(body=selected, type_ignores=[]),
                    "max-flow-reference", "exec"),
            namespace,
        )
        network = namespace["MaxFlow"](6)
        for source, target in [
            (0, 1), (0, 2),
            (1, 3), (1, 4), (2, 3),
            (3, 5), (4, 5),
        ]:
            network.add_edge(source, target, 1)
        self.assertEqual(network.max_flow_edmonds_karp(0, 5), 2)

    def test_queue_reference_respects_simulation_horizon(self):
        code = python_block_after(
            ALGORITHMS / "06-综合类算法说明.md",
            "## 2. 排队论",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "queue-reference", "exec"), namespace)
        result = namespace["simulate_mm1_queue"](
            0.8, 1.2, simulation_time=100.0, seed=2026
        )
        self.assertLessEqual(max(result["time"]), 100.0)
        self.assertGreaterEqual(result["utilization"], 0.0)
        self.assertLessEqual(result["utilization"], 1.0)
        self.assertEqual(
            result["customers_arrived"] - result["customers_served"],
            result["customers_censored_at_horizon"],
        )
        with self.assertRaises(ValueError):
            namespace["mm1_queue_metrics"](1.0, 0.0)

    def test_monte_carlo_reference_is_seeded_and_reports_assumptions(self):
        code = python_block_after(
            ALGORITHMS / "06-综合类算法说明.md",
            "## 1. 蒙特卡洛模拟",
        )
        namespace = {"__name__": "reference_test"}
        exec(compile(code, "monte-carlo-reference", "exec"), namespace)
        kwargs = dict(n=20000, confidence_level=0.95, seed=2026)
        first = namespace["monte_carlo_integral"](
            lambda values: values**2, 0.0, 1.0, **kwargs
        )
        second = namespace["monte_carlo_integral"](
            lambda values: values**2, 0.0, 1.0, **kwargs
        )
        self.assertEqual(first["estimate"], second["estimate"])
        self.assertAlmostEqual(first["estimate"], 1 / 3, delta=0.01)
        self.assertEqual(first["seed"], 2026)
        self.assertLess(first["confidence_interval"][0], first["confidence_interval"][1])

    def test_reference_snippets_do_not_globally_suppress_warnings(self):
        hits = []
        for path in ALGORITHMS.glob("*.md"):
            if "warnings.filterwarnings('ignore')" in path.read_text(encoding="utf-8"):
                hits.append(path.name)
        self.assertEqual(hits, [])

    def test_plot_helpers_do_not_execute_show_at_module_import(self):
        hits = []
        for path in ALGORITHMS.glob("*.md"):
            if re.search(r"(?m)^plt\.show\(\)\s*$", path.read_text(encoding="utf-8")):
                hits.append(path.name)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
