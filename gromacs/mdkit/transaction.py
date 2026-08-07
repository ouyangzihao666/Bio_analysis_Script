"""Transactional step execution: run in a stage dir, commit on success."""

from __future__ import annotations

import os
import shutil
from typing import Dict, List

from mdkit.exceptions import StepError


class Transaction:
    """Runs a step inside ``<step_dir>/.stage`` and commits declared outputs."""

    def __init__(self, step_dir: str, stage_name: str = ".stage"):
        self.step_dir = os.path.abspath(step_dir)
        self.stage_dir = os.path.join(self.step_dir, stage_name)

    def begin(self) -> str:
        if os.path.isdir(self.stage_dir):
            shutil.rmtree(self.stage_dir)
        os.makedirs(self.stage_dir, exist_ok=True)
        return self.stage_dir

    def commit(
        self,
        outputs: Dict[str, str],
        optional: Dict[str, bool],
    ) -> Dict[str, str]:
        """Move declared outputs from stage to the step dir.

        ``outputs`` maps logical name -> relative filename (within stage).
        ``optional`` maps logical name -> whether absence is tolerated.
        Returns dict logical -> final absolute path.
        """
        os.makedirs(self.step_dir, exist_ok=True)
        final = {}
        missing = []
        for logical, rel in outputs.items():
            src = os.path.join(self.stage_dir, rel)
            if not os.path.isfile(src):
                if optional.get(logical):
                    continue
                missing.append("%s (%s)" % (logical, rel))
                continue
            final[logical] = os.path.join(self.step_dir, rel)
        if missing:
            raise StepError(
                "步骤输出缺失: %s" % ", ".join(missing)
            )
        # All required outputs verified in stage before touching step dir.
        for logical, rel in outputs.items():
            if logical not in final:
                continue
            src = os.path.join(self.stage_dir, rel)
            dst = os.path.join(self.step_dir, rel)
            os.replace(src, dst)
        shutil.rmtree(self.stage_dir, ignore_errors=True)
        return final

    def abort(self, keep_stage: bool = True) -> None:
        if not keep_stage:
            shutil.rmtree(self.stage_dir, ignore_errors=True)


def remove_step_dir(step_dir: str) -> None:
    """Remove a managed step directory (used by ``clean``)."""
    if os.path.isdir(step_dir):
        shutil.rmtree(step_dir)
