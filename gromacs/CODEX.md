# CODEX 编排 mdkit 指南

本文档说明 Codex（或任何自动化代理）如何安全地规划、执行、监测和干预 mdkit 模拟运行。

## 接口约定

- 所有子命令支持 `--json`，输出为 UTF-8 JSON，字段稳定（`run`/`systems`/`steps`/`summary`/`failures`）
- 退出码：`0` 成功；`1` 配置/环境错误；`2` 有步骤失败；`130` 用户中断
- 步骤状态：`pending / running / done / skipped / failed / stale / awaiting_input / interrupted`
- 体系状态：`pending / running / done / failed / paused / interrupted`
- `mdkit status --json` 对运行中的 mdrun 注入 `progress`：`{step, time_ps, nsteps, percent}`
- 运行目录：`work_dir`（来自 systems.yaml `work_dir` 或 `--work-dir`），状态文件 `run_status.json`，锁文件 `.mdkit.lock`

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

# 4. 失败时取报告：出错步骤、命令、退出码、stderr 尾部
mdkit report result --json

# 5. 干预放行（确认 manual_check / 修复输入后）
mdkit skip result <system> <step> --reason "修复了配体电荷" [--output logical=path]

# 6. 重跑 / 回退 / 清理
mdkit retry result <system> <step>
mdkit rollback result <system> <step>
mdkit clean result <system> --from <step> --yes   # 删除失效输出，谨慎使用

# 7. 断点续跑 / 强制重跑
mdkit run -w ... -s ... --from <step> --force
```

## 安全规则（Codex 必须遵守）

1. **不修改用户模板与输入**：mdp 覆盖只渲染到步骤目录副本；需要改输入时先 `ctx.copy_input()` 到步骤目录再改。
2. **回退只失效、不删除**：`rollback` 不删文件；删除必须显式 `clean --yes`，生产轨迹删除前先向用户说明。
3. **坏运行不覆盖好输出**：步骤失败后正式目录保持原状（事务化），不要用 `rm` 手动清理；用 `clean`。
4. **不绕过 `ctx.run_gmx()`**：外部步骤也必须经统一的命令执行器（无 shell、超时、日志、组选择校验）。
5. **干预点**：`awaiting_input` 状态必须先 `skip`（放行）或 `retry`（重跑），不要直接改 `run_status.json`。
6. **环境**：用 `bioAna_gmx_user02` 环境执行；`doctor` 明确报缺失的工具不要硬跑配体步骤。

## 状态机速查

```
pending → running → done
                ↘ failed ──(on_failure=pause)──▶ awaiting_input ──skip──▶ skipped
                ↘ failed ──(continue)──▶ 本体系剩余步骤 skipped，其他体系继续
done ──(参数/输入变化)──▶ stale（重算）
running ──(SIGINT/SIGTERM)──▶ interrupted（可 --from 续跑）
```
