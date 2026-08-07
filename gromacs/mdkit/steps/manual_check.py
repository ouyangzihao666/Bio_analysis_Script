"""Human/Codex intervention gate."""

from __future__ import annotations

from mdkit.steps.base import Step


class ManualCheckStep(Step):
    name = "manual_check"
    version = "1.0"
    description = "人工/Codex 干预点：暂停等待确认后放行"
    inputs = []
    outputs = []
    param_schema = {
        "message": {
            "type": str,
            "default": "请检查中间产物；确认后执行: mdkit skip <run_dir> <system> manual_check --reason '...'",
        }
    }

    def run(self, ctx) -> None:
        # The runner handles awaiting; reaching here means it was released.
        ctx.log.info("manual_check 已放行")
