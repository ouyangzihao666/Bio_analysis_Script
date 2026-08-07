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

    def test_rename_molecule_preserves_leading_indent(self):
        top = self.ws.write(
            "itp.top",
            "[ moleculetype ]\n;name            nrexcl\n FDME             3\n",
        )
        gro = self.ws.write(
            "lig.gro",
            "lig\n    1\n    1  MOL   O1    1   0.494  -1.555   0.403\n"
            "   2.000   2.000   2.000\n",
        )
        topology.rename_molecule(top, gro, "FDME")
        with open(top, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn(" FDME             3", content)
        self.assertNotIn("FDMEE", content)
        with open(gro, encoding="utf-8") as fh:
            self.assertIn("    1FDME", fh.read())

    def test_merge_ligand_itps_dedupes_atomtypes(self):
        itp1 = self.ws.write(
            "l1.itp",
            "[ atomtypes ]\n;name\n c3  c3  0.0  0.0  A  0.34  0.11\n"
            "[ moleculetype ]\nL1 3\n[ atoms ]\n1 c3 1 L1 C1 1 0.0 12.0\n",
        )
        itp2 = self.ws.write(
            "l2.itp",
            "[ atomtypes ]\n;name\n c3  c3  0.0  0.0  A  0.34  0.11\n"
            " oh  oh  0.0  0.0  A  0.31  0.08\n"
            "[ moleculetype ]\nL2 3\n[ atoms ]\n1 oh 1 L2 O1 1 0.0 16.0\n",
        )
        out = os.path.join(self.ws.root, "merged.itp")
        topology.merge_ligand_itps([itp1, itp2], ["L1", "L2"], out)
        with open(out, encoding="utf-8") as fh:
            content = fh.read()
        self.assertEqual(content.count("[ atomtypes ]"), 1)
        self.assertIn("[ moleculetype ]\nL1 3", content)
        self.assertIn("[ moleculetype ]\nL2 3", content)
        self.assertIn(" oh  oh", content)

    def test_merge_ligand_itps_conflict_raises(self):
        itp1 = self.ws.write(
            "l1.itp",
            "[ atomtypes ]\nc3  c3  0.0  0.0  A  0.34  0.11\n"
            "[ moleculetype ]\nL1 3\n",
        )
        itp2 = self.ws.write(
            "l2.itp",
            "[ atomtypes ]\nc3  c3  9.9  9.9  A  0.99  0.99\n"
            "[ moleculetype ]\nL2 3\n",
        )
        out = os.path.join(self.ws.root, "merged.itp")
        with self.assertRaises(Exception):
            topology.merge_ligand_itps([itp1, itp2], ["L1", "L2"], out)


if __name__ == "__main__":
    unittest.main()
