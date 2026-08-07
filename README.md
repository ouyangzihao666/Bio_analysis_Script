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

`mdkit plan` 会按工作流顺序打印每一步将执行的**真实命令**（含 gmx 组选择输入），
不产生任何副作用；`mdkit status` 对运行中的 mdrun 显示**当前步数/总步数/百分比/时间**
（例如 `md running step 2780000/5000000 (55.6%), t=5560.0 ps`），`--json` 同步输出 `progress` 字段。

## 提示词模板（交给 AI/Codex 执行）

把下面整段复制给 Codex（或任何支持 shell 的 AI），按实际路径替换尖括号内容即可：

```text
请使用 /home/user02/bioAnalysis/git/gromacs/mdkit 完成一批 GROMACS MD 模拟。

环境：conda activate bioAna_gmx_user02；把 /home/user02/bioAnalysis/git/gromacs/mdkit 加入 PATH。

任务：
1. 先运行 mdkit doctor 确认 gmx/obabel/antechamber/acpype/PyYAML 可用；
2. 运行 mdkit plan -w <workflow.yaml> -s <systems.yaml> --work-dir <输出目录>，
   向用户展示将执行的每条命令，确认无误；
3. 用 tmux 启动 mdkit run（每个体系一个独立 --work-dir，便于并行与续跑），
   输出 --json；
4. 定期运行 mdkit status <run_dir> --json 检查进度（含 mdrun 当前步数/百分比）；
5. 若有步骤失败，运行 mdkit report <run_dir> --json 取错误与 stderr 尾部，
   判断是配置问题还是输入结构问题，向用户说明原因并给出解决方案
   （例如：多片段合并配体需人工拆分、结构硬冲突需修正姿态），
   修复后 mdkit retry/run --from 续跑；
6. 全部完成后运行分析：mdkit run -w workflow_analysis.yaml -s systems.yaml --work-dir <run_dir>。
```

## 无 AI 人工操作指南

即使没有 AI 参与，也可以按以下顺序完成模拟（每个命令的输出都会明确告诉下一步该做什么）：

```bash
# 1. 环境与输入检查（缺失工具会明确报错并给出安装命令）
mdkit doctor

# 2. 预览：确认步骤顺序、参数和每条真实命令（不执行、不建目录）
mdkit plan -w configs/workflow_complex.yaml -s configs/systems_example.yaml --work-dir ./result

# 3. 执行（失败会继续处理其他体系；每个体系一个独立 work-dir 便于并行）
mdkit run -w configs/workflow_complex.yaml -s configs/systems_example.yaml \
          --system complex_C --work-dir ./result/complex_C --json

# 4. 查看进度（md 阶段显示 step/总步数/百分比）
mdkit status ./result/complex_C

# 5. 出错时看报告，按提示修复后续跑
mdkit report ./result/complex_C
mdkit retry ./result/complex_C complex_C <出错的步骤>
mdkit run -w ... -s ... --system complex_C --work-dir ./result/complex_C --from <步骤>

# 6. 模拟完成后做分析
mdkit run -w configs/workflow_analysis.yaml -s configs/systems_example.yaml --work-dir ./result
```

常见需要人工介入的情况及对应输出：

- 配体拓扑包含多个不连接片段（多个小分子共用同一残基名）：`ligand_prep` 直接失败，
  错误信息说明"无法自动拆分"，请人工拆分后为每个小分子提供独立 sdf/mol2 文件；
- 输入结构存在硬冲突（如原子间距 <0.15 nm）：EM 无法收敛、NVT 报 LINCS 错误，
  `mdkit report` 会给出原子对坐标，需修正配体姿态或结构后重试；
- `manual_check` 步骤会暂停等待，`mdkit skip ... --reason "确认"` 放行。

## 配置说明

### workflow.yaml

```yaml
name: complex-md
failure_policy: continue      # continue: 单体系失败不影响其他体系；stop: 立即停止
layout: per_step              # per_step（默认，NN_步骤名 分目录）或 flat（体系目录平铺）
stage_name: .stage            # 可选；每步的临时工作区目录名（默认 .stage，在步骤目录内）
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

**`failure_policy` 的作用范围**：它是 workflow 级设置，作用于本次 `mdkit run` 的所有体系。
`continue`（默认）＝某体系某步失败后，该体系剩余步骤标记为 skipped，继续处理下一个体系；
`stop`＝出错后立即终止整个 run（当前体系剩余步骤不再执行，后续体系也不再启动），进程以退出码 2 结束。
它**不影响其他并行的 run**：每个 run 是独立进程、独立 work-dir、独立状态文件，
一个 run 的 stop 不会波及别的 run。

**临时工作区 `.stage`**：每个步骤在 `NN_步骤/.stage/` 内执行，成功后输出原子移入步骤目录、
`.stage` 被清理；失败时 `.stage` 保留供调试。目录名可通过 workflow 的 `stage_name` 修改
（仅限步骤目录内的简单目录名）。

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

配体条目支持的字段：

| 字段 | 说明 |
| --- | --- |
| `file` | 配体文件：`.sdf` / `.mol2` / `.pdb`（格式自动识别，可用 `format:` 指定） |
| `name` | 配体名（≤5 字符）；缺省取文件名 |
| `charge` / `count` | 净电荷 / 拷贝数 |
| `residue` | 配体位于蛋白 PDB 内时的 HETATM 残基名（如 `UNK`）；设置后自动提取，并在 pdb2gmx 前清洗该残基 |
| `names` | 多分子 mol2 拆分后的命名列表（长度须与分子数一致） |
| `split` | 多分子 mol2 是否自动拆分（默认 true） |
| `method: manual` | 使用自带 itp/gro（配合 `itp_file`/`gro_file`） |

多分子 mol2（如 `FDME-BDO.mol2` 含两个 `@<TRIPOS>MOLECULE` 段）会自动按段拆分为多个配体：优先用 `names:` 命名，否则用 mol2 的子结构名；若子结构重名会自动加后缀（如 `FME_2`）并在 `plan`/运行日志中提示人工确认——同配体多拷贝建议改用单分子文件 + `count`。

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
