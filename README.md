# Bio_analysis_Script

生物信息学分析脚本仓库：

- `foldx/`：FoldX 自动化脚本（修复、点突变、扫描等）
- `gromacs/`：**mdkit** —— 模块化 GROMACS MD 工作流工具

## mdkit 简介

mdkit 用 Python 统一编排 GROMACS MD 流程，覆盖纯蛋白、单/多配体复合物（不同配体可多拷贝 × 单体/多聚体蛋白），支持自定义工作流、断点续跑、进度查询、错误报告与回退；所有命令支持 `--json`，便于脚本与 AI 编排。

## 环境要求

- Python 3.9+，PyYAML，GROMACS（2021+，推荐 2024+）
- 配体参数化需要 OpenBabel、AmberTools（antechamber/parmchk2）、acpype；纯蛋白流程仅需 gmx
- 复合物拆分（`split_complex` / `pymol_split_ligand`）需要 PyMOL（headless：`pymol -cq` 可用）

创建 mdkit 专用 conda 环境（推荐）：

```bash
conda env create -f gromacs/environment.yml
conda activate mdkit
```

GROMACS 需在 PATH 中：可使用系统安装的 gmx，或 `conda install -c conda-forge gromacs` 安装 CPU 版。

## 快速开始

```bash
cd gromacs
conda env create -f environment.yml
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
`status` 也可直接指向 run 的父目录（如 `bench` 的 `<work-dir-base>/<测试>/`），会自动发现其下所有 run 并逐个显示。

## 配置

- `workflow.yaml`：`failure_policy: continue|stop`（默认 continue）、`layout: per_step|flat`、可选 `stage_name`（临时工作区目录名，默认 `.stage`）、`mdp_dir`、`steps` 列表；`defaults` / 步骤 `params` / 体系 `overrides` 三层参数合并。
- `systems.yaml`：体系清单。蛋白支持 `file` 或 `chains`（多聚体）；配体支持 `sdf/mol2/pdb`、`names`（多分子 mol2/sdf 拆分）、`method: manual`（自带 itp/gro）。
- `complex` 块：复合物体系用 `complex.file` + `complex.ligands`（每项 `name`（1-5 字符，即 PDB 残基名）与 `charge`，可选 `chain`）替代 `protein`/外部 `ligands`。`split_complex` 用 PyMOL 按残基名拆出蛋白与各配体；同名配体按 resid 升序与配置顺序对应，拆分文件加 resid 后缀（如 `UNK_501.pdb`），GROMACS 分子名保持基名，`[ molecules ]` 中同名合并计 count。

```yaml
systems:
  - name: complex_X
    complex:
      file: inputs/complex.pdb
      ligands:
        - name: UNK
          charge: 0
        - name: UNK      # 同名第二拷贝：按 resid 顺序取出并加后缀
          charge: 0
```

`workflow_complex.yaml` 的步骤顺序：`env_check` → `split_complex` → `split_ligand`（可换 `pymol_split_ligand`）→ `protein_prep` → `ligand_prep` → `complex_merge` → ...；拆分步骤设 `on_failure: pause`，未匹配/同名歧义时暂停等待人工处理。拓扑合并时 itp 按文件名逐个 include（每组同名只 include 一份），`[ molecules ]` 按 `gmx_name` 合并 count；结构 gro 逐份合并，保证原子数与拓扑一致。
- `systems.yaml` 顶层资源：`slots` 为可复用模板池（`0: "-ntmpi 1 -ntomp 32 -gpu_id 0"`，缺省单模板空参数），`concurrency` 为并发上限（默认 = 模板数，可大于模板数复用模板）；每体系 `slot: N` 为显式模板绑定（缺省任意空闲模板）。并行示例见 `configs/systems_example_parallel.yaml`。
- mdp：内置模板（ions/minim/nvt/npt/md），`mdp_overrides` 覆盖渲染，模板不被修改。

`failure_policy` 为 workflow 级，作用于本次 run 的所有体系；`stop` 时整个 run 终止（当前体系剩余步骤与后续体系不再执行），不影响其他并行 run。

## 运行控制

| 命令 | 作用 |
| --- | --- |
| `skip RUN SYS STEP --reason ... [--output logical=path]` | 人工/agent 放行（manual_check、失败暂停），可补录输出 |
| `retry` / `rollback` | 重置步骤及下游为待执行（rollback 不删文件） |
| `retry ... --select KEY` / `ctl retry ... --select KEY` | 同名配体歧义时选择候选（候选见 `ctl status`） |
| `clean RUN SYS --from STEP --yes` | 删除失效输出（需确认） |
| `run --from STEP --force` | 断点续跑 / 强制重跑 |
| `batch` | 按资源槽位并发运行多个体系（槽位参数原样透传，选项级去重） |
| `bench` | 基准测试套件：串行多测试、槽位并发、3–7ns 窗口采样 GPU/CPU 占用 |
| `ctl` | 统一控制入口：状态/队列/执行/干预（详见下方批量队列） |
| `watch` | 常驻执行器：消费 queue.json 自动启动/重跑；或监督单个 run 目录 |

示例：`mdkit bench -w workflow.yaml -s systems.yaml --work-dir-base ./bench --suite bench.yaml`。
`bench.yaml` 中每个测试定义 `name`、`slots`（mdrun 额外参数串，如
`"-ntomp 32 -gpu_id 1 -pinoffset 64"`）与 `systems`；同一 systems.yaml 可复用于不同工作流，
overrides 中不属于当前工作流的步骤会被忽略。

## 批量队列（ctl / watch）

批量 MD（多体系并发 + 人工干预自动补跑）需要准备的文件：

1. `workflow.yaml`（步骤与参数，可用 `configs/` 示例改 `mdp_dir`）
2. `systems.yaml`（体系清单 + 顶层 `slots`/`concurrency` + 每体系可选 `slot` 绑定）
3. 输入结构文件（蛋白 PDB、配体 sdf/mol2/pdb，路径在 systems.yaml 引用）
4. mdp 模板（默认内置 `configs/mdp/`，自定义用 `mdp_dir` 指向）
5. 环境：mdkit conda 环境 + PATH 里的 gmx；配体体系还需 obabel/antechamber/parmchk2/acpype；复合物体系还需 pymol

```bash
mdkit ctl init -w configs/workflow_complex.yaml -s configs/systems.yaml --work-dir-base ./batch
mdkit ctl exec start -q ./batch/queue.json
mdkit ctl status -q ./batch/queue.json            # 查看进度/模板占用
mdkit ctl retry -q ./batch/queue.json <system>    # 修复输入后重排（默认 checkpoint 续跑）
mdkit ctl retry -q ./batch/queue.json <system> --force   # 从头重跑
mdkit ctl queue hold/release -q ./batch/queue.json --system <system>
mdkit ctl exec stop -q ./batch/queue.json
```

`queue.json` 由 `ctl init` 生成在 `--work-dir-base/`，运行期由 `ctl` 与 `watch`
（`.mdkit.queue.lock` 互斥）原子读写，不要手改。watch 默认 3 分钟轮询一次状态、
30 分钟修复等待（`--interval`/`--repair-timeout` 可调，单位分钟）；退出码
0=全部完成、2=修复超时/失败、130=人工停止。

## 并行运行实操步骤（逐步）

下面以 `configs/systems_example_parallel.yaml` 为模板，演示从零开始并发跑多个体系。

1. **准备输入结构**：把蛋白 PDB、配体 sdf/mol2/pdb 放到 `configs/inputs/`（或自定目录），
   并修改 `systems_example_parallel.yaml` 中每个体系的 `protein.file` / `ligands[].file`
   为真实路径（复合物体系改用 `complex.file` + `complex.ligands`）；按你的 GPU/CPU 调整
   `slots` 参数串与 `concurrency`。

2. **准备工作流**：复制 `configs/workflow_complex.yaml` 为 `workflow.yaml`，确认
   `mdp_dir` 指向有效模板目录（默认内置 `configs/mdp/`）。

3. **预检与预演**（不产生副作用）：
   ```bash
   mdkit doctor
   mdkit plan -w workflow.yaml -s configs/systems_example_parallel.yaml --work-dir ./batch
   ```
   确认每个体系的步骤、mdp 渲染与每条真实命令符合预期。

4. **建队列并启动执行器**：
   ```bash
   mdkit ctl init -w workflow.yaml -s configs/systems_example_parallel.yaml \
     --work-dir-base ./batch
   mdkit ctl exec start -q ./batch/queue.json --interval 3 --repair-timeout 30
   ```
   各体系的 run 目录在首次启动时惰性创建于 `./batch/<体系名>/`；watch 日志写在
   `./batch/watch.log`。

5. **监控进度**：
   ```bash
   mdkit ctl status -q ./batch/queue.json            # 每体系状态 + 模板占用
   mdkit ctl status -q ./batch/queue.json --json     # 供脚本/AI 轮询
   mdkit ctl exec status -q ./batch/queue.json       # watch 是否在跑
   ```

6. **失败与人工修复**：某体系失败时，其他体系不受影响继续跑。修复输入/参数后：
   ```bash
   mdkit ctl retry -q ./batch/queue.json <体系名>            # 默认优雅中断+checkpoint 续跑
   mdkit ctl retry -q ./batch/queue.json <体系名> --force     # 该步骤从头重跑
   mdkit ctl retry -q ./batch/queue.json <体系名> <step> --select <key>  # 同名配体歧义选择
   ```
   watch 会在下一个轮询周期自动补跑，无需重启。

7. **收尾**：
   ```bash
   mdkit ctl exec stop -q ./batch/queue.json   # 首次：停止接收新任务，等在跑任务结束后退出（130）
   mdkit ctl exec stop -q ./batch/queue.json   # 再次：强制终止在跑任务（含 gmx 子进程）后退出（130）
   mdkit report ./batch/<体系名>               # 失败体系的错误与 stderr 尾部
   ```

临时改并发参数而不动文件：`ctl init --concurrency N --slot "参数串" --slot ...`
（`--slot` 追加为数字自动续编的新模板，`--concurrency` 覆盖文件值）。

## 一致性机制

- 输入不可变：步骤不修改输入，需改动时复制到自身目录；输出只落在自身步骤目录
- 事务化：每步在 `.stage` 临时区执行，成功后原子提交到步骤目录；失败保留 `.stage` 供调试
- 签名溯源：步骤名 + 版本 + 参数哈希（含 mdp 覆盖）+ mdp 模板内容哈希 + 输入文件哈希；配置、模板或输入变化自动使下游失效重算（注意：此改动会使升级前已完成的运行首次重跑时重算一次）

## 提示词模板（交给 AI/agent）

```text
使用 <gromacs仓库>/gromacs/mdkit 完成 GROMACS MD 模拟：
1. mdkit doctor 检查环境（gmx/obabel/antechamber/acpype/pymol/PyYAML）
2. mdkit plan 展示每条命令并确认
3. tmux 中按体系启动 mdkit run（独立 --work-dir，--json）
4. 定期 mdkit status --json 检查进度（含 mdrun 步数/百分比）
5. 失败时 mdkit report --json 取错误，判断配置或输入结构问题，
   说明原因与方案后修复并 retry / --from 续跑
6. 完成后运行 analysis 工作流
```

## 人工介入场景

- 多分子 mol2/sdf 配体文件：`split_ligand`（确定性解析器）或 `pymol_split_ligand`（PyMOL）按 `names` 匹配拆分；未匹配时步骤报错并列出文件内全部分子名，`on_failure: pause` 下进入 `awaiting_input`，修改 `names` 后 `ctl retry`
- 文件内同名配体（候选多于需求）：步骤进入等待，`ctl status` 展示候选，用 `ctl retry <system> <step> --select <key>` 选择后自动继续
- 复合物 PDB / 配体 PDB 含多个 MODEL：直接报错，需拆分为单 MODEL 文件
- 复合物同名配体（如两个 UNK）：`split_complex` 按 resid 顺序取出并加 resid 后缀（`UNK_501.pdb`）；配置数量与 PDB 残基数不一致时报错并列出全部 resid
- acpype 输出与配置名不一致：报错会列出 `*.acpype` 目录中实际的 `<名>_GMX.itp`，真实“配体名”= 去掉 `_GMX` 的基名，需与配置名一致（拆分步骤已自动把 mol2 内 MOLECULE 名规范化为 `gmx_name`）
- EM 不收敛或 NVT 报 LINCS 错误（常见原因：输入结构硬冲突、原子间距过小）：检查并修正配体姿态或结构
- `manual_check`：暂停等待，`skip --reason` 放行

拆分步骤（`split_complex`/`split_ligand`/`pymol_split_ligand`）只应出现在配体工作流中；纯蛋白体系不应包含拆分步骤（会明确报错）。

## 开发新步骤

`mdkit new-step <name> [--dir DIR]` 生成脚手架。步骤需实现 `inputs/outputs/param_schema/run(ctx)`，gmx 必须走 `ctx.run_gmx()`，失败抛 `StepError`。详见 `gromacs/CODEX.md`。

## 旧脚本

旧版 GROMACS 脚本已从当前分支移除（Git 历史与 Release 中可获取）。
