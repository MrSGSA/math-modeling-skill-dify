# MATLAB 实现规范

MATLAB 与 Python 是同等支持的实现语言，不把 MATLAB 作为仅供参考的附录。

当题目以矩阵运算、非线性方程组、连续优化、微分方程、符号推导、控制系统或交互式数值试验为核心，且所需工具箱可用时，应主动把 MATLAB 列为首选候选；不要先写 Python 后再把 MATLAB 当作形式性翻译。最终仍以用户偏好、现有代码、依赖许可和可复现性决定。

## 环境与依赖

调用 `../scripts/check_matlab_env.m`，只检查选中功能对应的工具箱。常见映射：

| 功能 | MATLAB 工具或工具箱 |
|---|---|
| 线性/非线性/整数优化 | Optimization Toolbox |
| 统计、回归、分类、聚类 | Statistics and Machine Learning Toolbox |
| 时间序列与计量 | Econometrics Toolbox |
| 符号推导 | Symbolic Math Toolbox |
| 图论与基础可视化 | MATLAB 基础环境 |

不要默认要求所有工具箱，也不要因缺少某个未使用工具箱而阻断任务。

## 代码结构

```matlab
function main(seed)
arguments
    seed (1,1) double = 42
end
rng(seed, "twister");

projectRoot = fileparts(mfilename("fullpath"));
data = readtable(fullfile(projectRoot, "data", "input.csv"));
result = solveModel(data);
writetable(result.table, fullfile(projectRoot, "results", "问题1_结果.csv"));
plotResults(result, fullfile(projectRoot, "figures"));
end
```

- 用 `fullfile` 构造路径，不依赖当前工作目录。
- 用 `arguments` 校验输入。
- 随机算法调用 `rng(seed, "twister")`。
- 函数文件与主运行脚本分离时，保持函数名和文件名一致。
- 表格使用 `readtable`、`writetable`；数值矩阵使用 `readmatrix`、`writematrix`。
- 优化结果必须检查 `exitflag` 和约束残差。

## 结果与复现

在复现清单中记录 MATLAB `version`、`ver` 中实际用到的工具箱版本、随机种子、输入 SHA-256、参数和唯一命令，例如：

```text
matlab -batch "main(42)"
```

生成清单时向 `repro_manifest.py` 传入 `--runtime matlab`、`--runtime-version` 和工具箱版本 JSON：

```powershell
python ../scripts/repro_manifest.py `
  --project-root "<PROJECT_ROOT>" --seed 42 `
  --runtime matlab --runtime-version "R2025b" `
  --dependencies '{"Optimization Toolbox":"25.2"}' `
  --command 'matlab -batch "main(42)"'
```

图使用 `exportgraphics` 输出 SVG 与 300 DPI PNG，不使用网格线。
