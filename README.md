# Bid_analysis_Script

生物信息学分析脚本仓库，目前包含：

- `foldx/`：FoldX 相关脚本
- `gromacs/`：**mdkit** —— 模块化 GROMACS MD 工具链（本仓库主推方案）

## mdkit 简介

mdkit 是一套基于 Python 的 GROMACS 分子动力学工作流工具，统一覆盖：

- **体系类型**：纯蛋白、单配体复合物、多配体复合物（不同配体可多拷贝）、多聚体蛋白（多链自动合并）
- **流程**：蛋白预处理 → 配体参数化 → 结构/拓扑合并 → 溶剂化 → 离子 → EM → NVT/NPT → 生产 MD → 轨迹校正 → 分析
- **自定义流程**：`workflow.yaml` 里任意增删、排序、复用步骤
- **监测与错误报告**：每步状态写入 `run_status.json`，`mdkit status/report` 可查询
- **Codex 可编排**：所有命令支持 `--json`、确定性退出码、断点续跑、人工/Codex 干预点
- **回退与一致性**：步骤事务化执行（坏运行不覆盖好输出）、输入/参数签名溯源、配置变更自动失效下游

## 环境要求

推荐在 `bioAna_gmx_user02` conda 环境中运行：

```bash
conda activate bioAna_gmx_user02   # Python 3.10 + ambertools
pip install pyyaml                 # 已装则跳过
```

- `gmx` 需在 PATH（GROMACS 2021+，建议 2024+）
- 配体参数化需要 `obabel`、`antechamber`、`parmchk2`、`acpype`
- 纯蛋白流程只需要 `gmx` 和 Python 3.9+

## 快速开始

```bash
cd gromacs

# 1. 环境检查
./mdkit/mdkit doctor

# 2. 预览工作流（不执行、不建目录）
./mdkit/mdkit plan -w configs/workflow_protein.yaml -s configs/systems_example.yaml

# 3. 执行（本机直接跑；多任务建议 tmux 分别启动）
./mdkit/mdkit run -w configs/workflow_protein.yaml -s configs/systems_example.yaml

# 4. 查询状态 / 汇总报告
./mdkit/mdkit status result --json
./mdkit/mdkit report result

# 5. 查看某步骤渲染后的有效 mdp
./mdkit/mdkit mdp-show -w configs/workflow_protein.yaml -s configs/systems_example.yaml --system protein_A nvt
```

## 配置说明

### workflow.yaml

```yaml
name: complex-md
failure_policy: continue      # continue: 单体系失败不影响其他体系；stop: 立即停止
layout: per_step              # per_step（默认，NN_步骤名 分目录）或 flat（体系目录平铺）
mdp_dir: mdp                  # 可选；内置模板目录，也可用绝对路径
dirs:                         # 可选；覆盖分析时的目录约定（*_md_production 等）
  md_production: "10_md_production"
steps:
  - step: env_check
    params:
      tools: [obabel, antechamber, parmchk2, acpype]
  - step: protein_prep
    params:
      force_field: amber99sb-ildn
      water_model: tip3p
  - step: ligand_prep
  - step: complex_merge
  - step: em
    params:
      mdp_overrides:          # mdp 参数覆盖，渲染后生效，模板不被修改
        nsteps: 20000
  - step: manual_check        # 可选干预点：暂停等待 mdkit skip 放行
    params:
      message: 请检查配体电荷后放行
```

步骤参数支持在工作流级 `defaults:`、步骤级 `params:`、体系级 `overrides:` 三层合并，后者优先级更高。每步可设 `on_failure: auto|pause`。

### systems.yaml

```yaml
work_dir: ./result
systems:
  - name: protein_A            # 纯蛋白
    protein: {file: inputs/protein_A.pdb}
    ligands: []

  - name: complex_C            # 双配体（LIG2 两拷贝）+ 体系级覆盖
    protein: {file: inputs/protein_C.pdb}
    ligands:
      - {name: LIG1, file: inputs/ligand_1.sdf, charge: 0}
      - {name: LIG2, file: inputs/ligand_2.sdf, charge: -1, count: 2}
    overrides:
      nvt: {mdp_overrides: {nsteps: 100000}}

  - name: multimer_D           # 多聚体蛋白（多链合并）+ 三配体
    protein:
      chains: [inputs/chain_A.pdb, inputs/chain_B.pdb]
    ligands:
      - {name: LIG1, file: inputs/ligand_1.sdf, charge: 0}
      - {name: LIG2, file: inputs/ligand_2.sdf, charge: 0}
      - {name: LIG3, file: inputs/ligand_3.sdf, charge: 0}

  - name: complex_manual       # 手动提供配体拓扑
    protein: {file: inputs/protein_D.pdb}
    ligands:
      - {name: MOL, method: manual, itp_file: inputs/MOL.itp, gro_file: inputs/MOL.gro}
```

## 运行控制与干预

| 命令 | 作用 |
| --- | --- |
| `mdkit skip <run_dir> <system> <step> --reason ... [--output logical=path]` | 人工/Codex 放行（manual_check、失败暂停），可补录输出 |
| `mdkit retry <run_dir> <system> [<step>]` | 把该步骤及下游重置为待执行 |
| `mdkit rollback <run_dir> <system> [<step>]` | 回退步骤并失效下游（不删文件） |
| `mdkit clean <run_dir> <system> --from <step> --yes` | 显式删除失效输出（生产轨迹昂贵，默认需确认） |
| `mdkit run ... --from <step> --force` | 断点续跑 / 强制重跑 |

## 一致性机制

- **输入不可变**：步骤不修改输入文件，需要改动时复制到自身目录；所有输出必须落在自己步骤目录内
- **事务化**：每步在 `.stage` 临时区执行，成功后才原子移入正式目录；失败时正式目录保持原状
- **签名溯源**：每步记录 步骤名+版本+参数哈希（含 mdp 模板哈希与覆盖）+输入文件 SHA-256；配置或输入变化自动使该步及下游 `stale` 重算，绝不误用旧输出

## 开发新步骤

```bash
./mdkit/mdkit new-step my_step                 # 内置 steps/ 目录
./mdkit/mdkit new-step my_step --dir ./mysteps # 外部步骤目录（workflow 中 steps_dir 指向）
```

步骤需实现固定接口：`inputs/outputs`（逻辑文件名）、`param_schema`、`run(ctx)`；禁止 `cd` 与全局状态，gmx 必须走 `ctx.run_gmx()`，失败抛 `StepError`。详见 `gromacs/CODEX.md`。

## 旧脚本说明

旧版 `MD-protein/`、`MD-complex/`、`tools/` 脚本已从当前分支移除（历史提交与 GitHub Releases `GROMACS`、`v1.0.1` 中仍可获取）。
