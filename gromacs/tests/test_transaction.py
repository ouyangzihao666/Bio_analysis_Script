"""Transaction (stage/commit) tests, including directory outputs."""

from __future__ import annotations

import os
import unittest

from mdkit.exceptions import StepError
from mdkit.transaction import Transaction

from tests.helpers import TempWorkspace


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    def test_commit_file_and_dir(self):
        tx = Transaction(os.path.join(self.ws.root, "step"))
        stage = tx.begin()
        os.makedirs(os.path.join(stage, "amber99sb-ildn.ff"), exist_ok=True)
        with open(os.path.join(stage, "amber99sb-ildn.ff", "forcefield.itp"), "w") as fh:
            fh.write("x")
        with open(os.path.join(stage, "top.top"), "w") as fh:
            fh.write("y")
        final = tx.commit(
            {"ff_dir": "amber99sb-ildn.ff", "top": "top.top"},
            {},
        )
        self.assertTrue(
            os.path.isfile(
                os.path.join(final["ff_dir"], "forcefield.itp")
            )
        )
        self.assertTrue(os.path.isfile(final["top"]))
        self.assertFalse(os.path.isdir(stage))

    def test_commit_missing_required_raises_and_keeps_step_dir(self):
        step_dir = os.path.join(self.ws.root, "step")
        tx = Transaction(step_dir)
        stage = tx.begin()
        os.makedirs(step_dir, exist_ok=True)
        with open(os.path.join(step_dir, "keep.txt"), "w") as fh:
            fh.write("keep")
        with self.assertRaises(StepError):
            tx.commit({"missing": "nope.gro"}, {})
        self.assertTrue(os.path.isfile(os.path.join(step_dir, "keep.txt")))

    def test_commit_optional_missing_ok(self):
        tx = Transaction(os.path.join(self.ws.root, "step"))
        stage = tx.begin()
        with open(os.path.join(stage, "a.txt"), "w") as fh:
            fh.write("a")
        final = tx.commit({"a": "a.txt", "opt": "opt.xtc"}, {"opt": True})
        self.assertIn("a", final)
        self.assertNotIn("opt", final)

    def test_custom_stage_name(self):
        tx = Transaction(os.path.join(self.ws.root, "step"), stage_name=".work")
        stage = tx.begin()
        self.assertTrue(os.path.isdir(os.path.join(self.ws.root, "step", ".work")))
        with open(os.path.join(stage, "a.txt"), "w") as fh:
            fh.write("a")
        final = tx.commit({"a": "a.txt"}, {})
        self.assertTrue(os.path.isfile(final["a"]))
        self.assertFalse(os.path.exists(os.path.join(self.ws.root, "step", ".work")))


if __name__ == "__main__":
    unittest.main()
