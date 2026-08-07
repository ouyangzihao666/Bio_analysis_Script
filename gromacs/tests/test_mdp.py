"""mdp template / override rendering tests."""

from __future__ import annotations

import os
import unittest

from mdkit.exceptions import ConfigError
from mdkit.mdp import render_mdp, resolve_template, sha256_text

from tests.helpers import TempWorkspace


class MdpTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.tpl = self.ws.write(
            "mdp/nvt.mdp",
            "; comment\nnsteps = 50000\nref_t = 298\n",
        )

    def tearDown(self):
        self.ws.cleanup()

    def test_resolve_builtin_and_path(self):
        mdp_dir = os.path.join(self.ws.root, "mdp")
        self.assertEqual(
            resolve_template("nvt", mdp_dir),
            os.path.join(mdp_dir, "nvt.mdp"),
        )
        self.assertEqual(
            resolve_template(os.path.join(mdp_dir, "nvt.mdp"), mdp_dir),
            os.path.join(mdp_dir, "nvt.mdp"),
        )
        with self.assertRaises(ConfigError):
            resolve_template("missing", mdp_dir)

    def test_render_replaces_and_appends(self):
        out = os.path.join(self.ws.root, "out", "nvt.mdp")
        info = render_mdp(
            self.tpl,
            {"nsteps": 10000, "tcoupl": "V-rescale", "gen_vel": True},
            out,
        )
        with open(out, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("nsteps = 10000", content)
        self.assertIn("ref_t = 298", content)
        self.assertIn("tcoupl = V-rescale", content)
        self.assertIn("gen_vel = yes", content)
        # Template untouched.
        with open(self.tpl, encoding="utf-8") as fh:
            self.assertIn("nsteps = 50000", fh.read())
        self.assertEqual(info["template"], os.path.abspath(self.tpl))
        self.assertTrue(info["template_sha256"])

    def test_render_hash_matches_text(self):
        with open(self.tpl, encoding="utf-8") as fh:
            text = fh.read()
        with open(self.tpl, encoding="utf-8") as fh2:
            self.assertEqual(sha256_text(text), sha256_text(fh2.read()))


if __name__ == "__main__":
    unittest.main()
