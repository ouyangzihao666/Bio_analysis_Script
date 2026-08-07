"""Topology include handling tests."""

from __future__ import annotations

import os
import unittest

from mdkit import topology

from tests.helpers import TempWorkspace


class TopologyTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    def test_local_include_absolutized_remote_kept(self):
        src_dir = os.path.join(self.ws.root, "src")
        os.makedirs(src_dir, exist_ok=True)
        posre = os.path.join(src_dir, "posre.itp")
        with open(posre, "w") as fh:
            fh.write("x")
        src = os.path.join(src_dir, "top.top")
        with open(src, "w") as fh:
            fh.write(
                '#include "posre.itp"\n'
                '#include "amber99sb-ildn.ff/forcefield.itp"\n'
                '#include "/abs/x.itp"\n'
            )
        dst = os.path.join(self.ws.root, "dst.top")
        topology.absolutize_includes(src, dst)
        with open(dst, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn('#include "%s"' % posre, content)
        self.assertIn(
            '#include "amber99sb-ildn.ff/forcefield.itp"', content
        )
        self.assertIn('#include "/abs/x.itp"', content)

    def test_append_molecules_and_rename(self):
        top = self.ws.write(
            "top.top",
            '[ moleculetype ]\n; name  nrexcl\nLIG 3\n\n[ molecules ]\nProtein 1\n',
        )
        topology.append_molecules(top, [("FDME", 1), ("BDO", 2)])
        with open(top, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("FDME", content)
        self.assertIn("BDO", content)
        self.assertRegex(content, r"FDME\s+1")
        self.assertRegex(content, r"BDO\s+2")


if __name__ == "__main__":
    unittest.main()
