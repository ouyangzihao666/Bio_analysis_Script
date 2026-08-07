"""Run summary / error report generation."""

from __future__ import annotations


def build_report(data: dict) -> dict:
    systems = data.get("systems", {})
    out = {"run": data.get("run", {}), "systems": [], "summary": {}}
    total_steps = 0
    counts = {"done": 0, "failed": 0, "skipped": 0, "pending": 0,
              "running": 0, "stale": 0, "awaiting_input": 0, "interrupted": 0}
    failures = []
    for name, sys_entry in systems.items():
        steps_out = []
        for step_name, st in sys_entry.get("steps", {}).items():
            status = st.get("status", "pending")
            total_steps += 1
            counts[status] = counts.get(status, 0) + 1
            step_rec = {
                "step": step_name,
                "status": status,
                "duration_s": st.get("duration_s"),
                "error": st.get("error"),
                "note": st.get("note"),
                "exit_code": st.get("exit_code"),
                "outputs": sorted(st.get("outputs", {}).keys()),
                "commands": st.get("commands", []),
            }
            if st.get("stderr_tail"):
                step_rec["stderr_tail"] = st["stderr_tail"][-4000:]
            if status == "failed":
                failures.append(
                    {
                        "system": name,
                        "step": step_name,
                        "error": st.get("error"),
                        "exit_code": st.get("exit_code"),
                        "stderr_tail": st.get("stderr_tail"),
                    }
                )
            steps_out.append(step_rec)
        out["systems"].append(
            {
                "name": name,
                "status": sys_entry.get("status", "pending"),
                "steps": steps_out,
            }
        )
    counts["total_systems"] = len(systems)
    counts["total_steps"] = total_steps
    counts["failed_systems"] = sum(
        1 for s in systems.values() if s.get("status") == "failed"
    )
    counts["done_systems"] = sum(
        1 for s in systems.values() if s.get("status") == "done"
    )
    out["summary"] = counts
    out["failures"] = failures
    return out


def render_text(report: dict) -> str:
    lines = []
    summary = report["summary"]
    lines.append("===== mdkit 运行报告 =====")
    lines.append(
        "体系: %d 完成 / %d 失败 / %d 总数"
        % (
            summary.get("done_systems", 0),
            summary.get("failed_systems", 0),
            summary.get("total_systems", 0),
        )
    )
    lines.append(
        "步骤: done=%d failed=%d skipped=%d awaiting=%d stale=%d interrupted=%d"
        % (
            summary.get("done", 0),
            summary.get("failed", 0),
            summary.get("skipped", 0),
            summary.get("awaiting_input", 0),
            summary.get("stale", 0),
            summary.get("interrupted", 0),
        )
    )
    for sys_rec in report["systems"]:
        lines.append("")
        lines.append("[%s] %s" % (sys_rec["name"], sys_rec["status"]))
        for st in sys_rec["steps"]:
            extra = ""
            if st["error"]:
                extra = "  错误: %s" % st["error"]
            elif st["note"]:
                extra = "  说明: %s" % st["note"]
            lines.append(
                "  %-16s %-14s %s%s"
                % (
                    st["step"],
                    st["status"],
                    ("%.1fs" % st["duration_s"]) if st["duration_s"] is not None else "",
                    extra,
                )
            )
    if report["failures"]:
        lines.append("")
        lines.append("===== 错误清单 =====")
        for f in report["failures"]:
            lines.append(
                "- [%s/%s] %s" % (f["system"], f["step"], f["error"])
            )
    return "\n".join(lines)
