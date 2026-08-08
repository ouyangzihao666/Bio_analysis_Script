# Bio_analysis_Script

生物信息学分析脚本仓库：

- `foldx/`：FoldX 自动化脚本（修复、点突变、扫描等）
- `gromacs/`：**mdkit** —— 模块化 GROMACS MD 工作流工具

## mdkit 简介

mdkit 用 Python 统一编排 GROMACS MD 流程，覆盖纯蛋白、单/多配体复合物（不同配体可多拷贝 × 单体/多聚体蛋白），支持自定义工作流、断点续跑、进度查询、错误报告与回退；所有命令支持 `--json`，便于脚本与 AI 编排。

## 环境要求

- Python 3.9+，PyYAML，GROMACS（2021+，推荐 2024+）
- 配体参数化需要 OpenBabel、AmberTools（antechamber/parmchk2）、acpype；纯蛋白流程仅需 gmx

创建 mdkit 专用 conda 环境（推荐）：

```bash
conda env create -f gromacs/environment.yml
conda activate mdkit
```

GROMACS 需在 PATH 中：可使用系统安装的 gmx，或 `conda install -c conda-forge gromacs` 安装 CPU 版。

## 快速开始

```bash
cd gromacs
conda env create -f gromacs/environment.yml
conda activate mdkit
export PATH=$PWD/mdkit:$PATH          # 把 mdkit 目录加入 PATH

mdkit doctor                           # 环境检查
mdkit plan -w configs/workflow_protein.yaml -s configs/systems_example.yaml --work-dir ./result
mdkit run   -w configs/workflow_protein.yaml -s configs/systems_example.yaml --system protein_A --work-dir ./result/protein_A
mdkit status ./result/protein_A
mdkit report ./result/protein_A        # 失败时的错误与 stderr 尾部
mdkit run -w configs/workflow_analysis.yaml -s configs/systems_example.yaml --work-dir ./result
```

`plan` 按工作流顺序打印每一步将执行的**真实命令**（无副作用）；`status` 对运行中的 mdrun 显示 `step 2780000/5000000 (55.6%), t=5560.0 ps`，`--json` 输出含 `progress`。
`status` 也可直接指向 run 的父目录（如 `bench` 的 `out/<测试>/`），会自动发现其下所有 run 并逐个显示。

## 配置

- `workflow.yaml`：`failure_policy: continue|stop`（默认 continue）、`layout: per_step|flat`、可选 `stage_name`（临时工作区目录名，默认 `.stage`）、`mdp_dir`、`steps` 列表；`defaults` / 步骤 `params` / 体系 `overrides` 三层参数合并。
- `systems.yaml`：体系清单；蛋白支持 `file` 或 `chains`（多聚体）；配体支持 `sdf/mol2/pdb`、`residue`（从蛋白 PDB 提取）、`names`（多分子 mol2 拆分）、`count`（多拷贝）、`method: manual`（自带 itp/gro）。
- mdp：内置模板（ions/minim/nvt/npt/md），`mdp_overrides` 覆盖渲染，模板不被修改。

`failure_policy` 为 workflow 级，作用于本次 run 的所有体系；`stop` 时整个 run 终止（当前体系剩余步骤与后续体系不再执行），不影响其他并行 run。

## 运行控制

| 命令 | 作用 |
| --- | --- |
| `skip RUN SYS STEP --reason ... [--output logical=path]` | 人工/agent 放行（manual_check、失败暂停），可补录输出 |
| `retry` / `rollback` | 重置步骤及下游为待执行（rollback 不删文件） |
| `clean RUN SYS --from STEP --yes` | 删除失效输出（需确认） |
| `run --from STEP --force` | 断点续跑 / 强制重跑 |
| `batch` | 按资源槽位并发运行多个体系（槽位参数原样透传，选项级去重） |
| `bench` | 基准测试套件：串行多测试、槽位并发、3–7ns 窗口采样 GPU/CPU 占用 |

示例：`mdkit bench -w workflow.yaml -s systems.yaml --work-dir-base ./bench --suite bench.yaml`。
`bench.yaml` 中每个测试定义 `name`、`slots`（mdrun 额外参数串，如
`"-ntomp 32 -gpu_id 1 -pinoffset 64"`）与 `systems`；同一 systems.yaml 可复用于不同工作流，
overrides 中不属于当前工作流的步骤会被忽略。

## 一致性机制

- 输入不可变：步骤不修改输入，需改动时复制到自身目录；输出只落在自身步骤目录
- 事务化：每步在 `.stage` 临时区执行，成功后原子提交到步骤目录；失败保留 `.stage` 供调试
- 签名溯源：步骤名 + 版本 + 参数哈希（含 mdp 覆盖）+ 输入文件哈希；配置或输入变化自动使下游失效重算

## 提示词模板（交给 AI/agent）

```text
使用 <gromacs仓库>/gromacs/mdkit 完成 GROMACS MD 模拟：
1. mdkit doctor 检查环境（gmx/obabel/antechamber/acpype/PyYAML）
2. mdkit plan 展示每条命令并确认
3. tmux 中按体系启动 mdkit run（独立 --work-dir，--json）
4. 定期 mdkit status --json 检查进度（含 mdrun 步数/百分比）
5. 失败时 mdkit report --json 取错误，判断配置或输入结构问题，
   说明原因与方案后修复并 retry / --from 续跑
6. 完成后运行 analysis 工作流
```

## 人工介入场景

- 多片段合并配体（多个小分子共用同一残基名）：`ligand_prep` 明确报错，需人工拆分为独立 sdf/mol2 文件
- 输入结构硬冲突（原子间距 <0.15 nm）：EM 不收敛、NVT 报 LINCS 错误，需修正配体姿态或结构
- `manual_check`：暂停等待，`skip --reason` 放行

## 开发新步骤

`mdkit new-step <name> [--dir DIR]` 生成脚手架。步骤需实现 `inputs/outputs/param_schema/run(ctx)`，gmx 必须走 `ctx.run_gmx()`，失败抛 `StepError`。详见 `gromacs/CODEX.md`。

## 旧脚本

旧版 GROMACS 脚本已从当前分支移除（Git 历史与 Release 中可获取）。
