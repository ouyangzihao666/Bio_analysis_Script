# CODEX 编排 mdkit 指南

本文档说明 Codex（或任何自动化代理）如何安全地规划、执行、监测和干预 mdkit 模拟运行。

## 接口约定

- 所有子命令支持 `--json`，输出为 UTF-8 JSON，字段稳定（`run`/`systems`/`steps`/`summary`/`failures`）
- 退出码：`0` 成功；`1` 配置/环境错误；`2` 有步骤失败；`130` 用户中断
- 步骤状态：`pending / running / done / skipped / failed / stale / awaiting_input / interrupted`
- 体系状态：`pending / running / done / failed / paused / interrupted`
- `mdkit status --json` 对运行中的 mdrun 注入 `progress`：`{step, time_ps, nsteps, percent}`
- 运行目录：`work_dir`（来自 systems.yaml `work_dir` 或 `--work-dir`），状态文件 `run_status.json`，锁文件 `.mdkit.lock`
- 拆分步骤：`split_complex`（PyMOL，复合物→蛋白+内嵌配体）、`split_ligand`（确定性解析器）、`pymol_split_ligand`（PyMOL 配体拆分），输出 logical 分别为 `split_protein_pdb`/`split_ligand_pdb:<name>` 与 `ligand_mol:<name>`；`protein_prep`/`ligand_prep` 自动优先消费拆分产物
- 等待选择：步骤抛 `ChoiceError` 时进入 `awaiting_input` 并记录 `st["choice"]`（question+candidates），用 `ctl retry <system> <step> --select <key>` 回答；回答写入 `st["choice_answer"]` 并在重跑时注入步骤
- 离子索引：`ions` 步骤的 `positive_ion`/`negative_ion` 为必要参数（无默认值），`index` 步骤据此生成 `Ion` 索引组

## 编排闭环

```bash
# 0. 环境检查（缺失工具会在 JSON 中给出 required/warn 标记）
mdkit doctor --json

# 1. 规划：确认步骤、目录、参数、mdp 与每条真实命令（不产生副作用）
mdkit plan -w configs/workflow_complex.yaml -s configs/systems.yaml --json

# 2. 执行（可与 tmux 结合；同一 run 目录只允许一个执行进程）
mdkit run -w configs/workflow_complex.yaml -s configs/systems.yaml --json

# 3. 轮询状态（状态文件原子写入，可安全并发读）
mdkit status result --json
#    也可指向父目录（bench 的 out/<测试>/），自动发现所有子 run 并显示

# 4. 失败时取报告：出错步骤、命令、退出码、stderr 尾部
mdkit report result --json

# 5. 干预放行（确认 manual_check / 修复输入后）
mdkit skip result <system> <step> --reason "修复了配体电荷" [--output logical=path]

# 6. 重跑 / 回退 / 清理
mdkit retry result <system> <step>
mdkit retry result <system> <step> --select <key>   # 同名配体歧义选择
mdkit rollback result <system> <step>
mdkit clean result <system> --from <step> --yes   # 删除失效输出，谨慎使用

# 7. 断点续跑 / 强制重跑
mdkit run -w ... -s ... --from <step> --force

# 8. 并发运行（资源槽位：槽位参数为 mdrun 额外参数，原样透传）
mdkit batch -w ... -s ... --work-dir-base ./batch \
  --slot "-ntomp 32 -gpu_id 1 -pinoffset 64" \
  --slot "-ntmpi 1 -ntomp 32 -gpu_id 0"

# 9. 基准测试（串行测试套件 + 3-7ns 窗口采样 GPU/CPU）
mdkit bench -w ... -s ... --work-dir-base ./bench --suite bench.yaml
```

`mdkit batch/bench` 的槽位参数与用户 extra_args 经**选项级去重合并**（gmx 不接受重复选项），
槽位按空闲状态分配，并发数 = 槽位数。bench 输出每个测试的 `benchmark.json`
（per-GPU 平均利用率、per-体系平均 CPU%、墙钟）。

## 批量队列（ctl / watch）——统一控制入口

`mdkit ctl` 是状态查询、队列控制、执行控制的统一入口；`mdkit watch` 是常驻执行器，
消费 `queue.json`，在“体系可启动（含人工干预后）+ 全局并发未满 + run 锁空闲”时自动
调用 `mdkit run`。适合“失败 → 人工修复 → retry → 自动补跑”的批量 MD 场景。

```bash
# 0. 建队列（slots/concurrency 来自 systems.yaml 顶层；--slot 可追加，数字自动续编）
mdkit ctl init -w configs/workflow_complex.yaml -s configs/systems.yaml \
  --work-dir-base ./batch [--concurrency 4]

# 1. 启动执行器（后台；日志 work_dir_base/watch.log）
mdkit ctl exec start -q ./batch/queue.json [--interval 3] [--repair-timeout 30]

# 2. 状态查询（每体系进度、模板占用、next-action 提示）
mdkit ctl status -q ./batch/queue.json --json
mdkit ctl exec status -q ./batch/queue.json   # watch 是否在跑

# 3. 队列控制
mdkit ctl queue list/hold/release/add/remove -q ./batch/queue.json --system <name>
mdkit ctl queue sync -q ./batch/queue.json    # 按 systems.yaml 增删/刷新绑定

# 4. 人工修复后干预（运行中体系默认优雅中断+checkpoint 续跑；--force 从头重跑）
mdkit ctl retry  -q ./batch/queue.json <system> [<step>] [--force]
mdkit ctl skip   -q ./batch/queue.json <system> <step> --reason "..."
mdkit ctl rollback / clean -q ./batch/queue.json <system> ...

# 5. 停止执行器（首次：停止接收新任务，等在跑任务结束；再次：强制终止在跑任务，
#    含 gmx 子进程；watch 退出码 130）
mdkit ctl exec stop -q ./batch/queue.json
```

复合物工作流示例步骤：`env_check` → `split_complex` → `split_ligand`（或 `pymol_split_ligand`）
→ `protein_prep` → `ligand_prep` → `complex_merge` → ...。拆分步骤建议设 `on_failure: pause`；
多分子 mol2/sdf 未匹配时步骤报错列出分子名并暂停，同名歧义用 `--select` 选择。

资源模型：`systems.yaml` 顶层 `slots` 是**可复用模板池**
（`0: "-ntmpi 1 -ntomp 32 -gpu_id 0"`，缺省单模板空参数），`concurrency` 为并发上限
（默认 = 模板数，可大于模板数，同一模板承载多个并发 job，需用户用 pinoffset 错开）；
每体系 `slot: N` 为显式绑定（缺省任意空闲模板，未绑定取占用最少模板）。

干预语义：对正在运行的体系，`ctl retry` 默认由 watch 发 SIGTERM 优雅中断（mdrun 写
checkpoint），应用干预后以 `-cpi` **从中断点续跑**；`ctl retry --force`（或 `ctl force`）
等同 Ctrl+C，中断后该 step **从头重跑**（修复输入/参数后使用）。

watch 退出码：`0` 全部完成；`2` 存在 repair-timeout/失败（`--repair-timeout` 默认 30min，
`--max-wait` 到期亦为 2）；`130` 人工停止（stop 标记/信号）。单 run 监督：
`mdkit watch <run_dir>`（串行，manual_check/失败暂停放行后自动继续）。

## 安全规则（Codex 必须遵守）

1. **不修改用户模板与输入**：mdp 覆盖只渲染到步骤目录副本；需要改输入时先 `ctx.copy_input()` 到步骤目录再改。
2. **回退只失效、不删除**：`rollback` 不删文件；删除必须显式 `clean --yes`，生产轨迹删除前先向用户说明。
3. **坏运行不覆盖好输出**：步骤失败后正式目录保持原状（事务化），不要用 `rm` 手动清理；用 `clean`。
4. **不绕过 `ctx.run_gmx()`**：外部步骤也必须经统一的命令执行器（无 shell、超时、日志、组选择校验）。
5. **干预点**：`awaiting_input` 状态必须先 `skip`（放行）或 `retry`（重跑），不要直接改 `run_status.json`。
6. **环境**：用 `mdkit` conda 环境（见 README 环境要求）执行；`doctor` 明确报缺失的工具不要硬跑配体步骤；复合物体系需 pymol。

## 状态机速查

```
pending → running → done
                ↘ failed ──(on_failure=pause)──▶ awaiting_input ──skip──▶ skipped
                ↘ failed ──(continue)──▶ 本体系剩余步骤 skipped，其他体系继续
done ──(参数/输入变化)──▶ stale（重算）
running ──(SIGINT/SIGTERM)──▶ interrupted（可 --from 续跑）
失败/同名歧义 ──(on_failure=pause / ChoiceError)──▶ awaiting_input ──(retry / retry --select)──▶ pending
```
