"""Environment check step."""

from __future__ import annotations

from mdkit import doctor
from mdkit.exceptions import StepError
from mdkit.steps.base import Step


class EnvCheckStep(Step):
    name = "env_check"
    version = "1.0"
    description = "检查 gmx、PyYAML 与所需工具是否可用"
    inputs = []
    outputs = []
    param_schema = {"tools": {"type": "list", "default": []}}

    def run(self, ctx) -> None:
        report = doctor.check_environment(tools=ctx.params.get("tools") or [])
        for check in report["checks"]:
            level = "OK" if check["ok"] else ("ERROR" if check["required"] else "WARN")
            ctx.log.info("[env] %s %s: %s", level, check["name"], check["detail"])
        missing = [
            c["name"] for c in report["checks"] if not c["ok"]
        ]
        if missing:
            raise StepError("环境检查失败，缺少必需项: %s" % ", ".join(missing))
