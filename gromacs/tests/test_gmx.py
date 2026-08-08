"""CommandRunner streaming/tee/timeout tests."""

from __future__ import annotations

import logging
import os
import threading
import time
import unittest

from mdkit.exceptions import CommandError
from mdkit.gmx import CommandRunner

from tests.helpers import TempWorkspace


class GmxRunnerTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.log = logging.getLogger("test_gmx")
        self.log.addHandler(logging.NullHandler())

    def tearDown(self):
        self.ws.cleanup()

    def test_tee_captures_live_stdout(self):
        tee = os.path.join(self.ws.root, "out.log")
        runner = CommandRunner(self.log)
        runner.run(
            [
                "python3",
                "-c",
                "import sys,time; sys.stdout.write('step 1, remaining wall clock time: 0 s\\n'); sys.stdout.flush(); time.sleep(0.2); sys.stdout.write('step 2, done\\n')",
            ],
            tee_path=tee,
        )
        with open(tee, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("remaining wall clock time", content)
        self.assertIn("step 2, done", content)

    def test_tee_splits_carriage_return_progress(self):
        # gmx mdrun -v 用 \r 覆盖进度行、很少写 \n；tee 必须按 \r 切分并实时落盘
        tee = os.path.join(self.ws.root, "out.log")
        runner = CommandRunner(self.log)
        result = {}

        def target():
            result["r"] = runner.run(
                [
                    "python3",
                    "-c",
                    "import sys,time;"
                    "sys.stdout.write('\\rstep 100, remaining wall clock time: 123 s');sys.stdout.flush();"
                    "time.sleep(1.0);"
                    "sys.stdout.write('\\rstep 200, will finish later');sys.stdout.flush()",
                ],
                tee_path=tee,
            )

        th = threading.Thread(target=target)
        th.start()
        # 进程结束前，tee 应已包含第一条 \r 进度（无需等待 \n）
        time.sleep(0.4)
        with open(tee, encoding="utf-8") as fh:
            early = fh.read()
        self.assertIn("step 100", early)
        self.assertIn("remaining wall clock time: 123 s", early)
        th.join(timeout=10)
        self.assertFalse(th.is_alive())
        self.assertEqual(result["r"]["returncode"], 0)
        with open(tee, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("step 200", content)

    def test_failure_tail(self):
        runner = CommandRunner(self.log)
        with self.assertRaises(CommandError) as ctx:
            runner.run(
                ["python3", "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(1)"]
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("boom", ctx.exception.output_tail)

    def test_timeout(self):
        runner = CommandRunner(self.log, timeout=1)
        with self.assertRaises(CommandError) as ctx:
            runner.run(["python3", "-c", "import time; time.sleep(10)"])
        self.assertTrue(ctx.exception.timed_out)


if __name__ == "__main__":
    unittest.main()
