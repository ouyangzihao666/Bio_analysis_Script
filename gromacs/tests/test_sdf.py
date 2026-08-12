"""SDF record parsing tests."""

from __future__ import annotations

import os
import unittest

from mdkit import sdf

from tests.helpers import TempWorkspace


class SdfTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()

    def tearDown(self):
        self.ws.cleanup()

    def test_parse_records_and_titles(self):
        path = self.ws.write(
            "multi.sdf",
            "molA\n  OpenBabel\n\n  3  2  0  0  0  0  0  0  0  0999 V2000\n"
            "$$$$\nmolB\n  OpenBabel\n\n  2  1  0  0  0  0  0  0  0  0999 V2000\n"
            "$$$$\n",
        )
        blocks = sdf.parse_molecules(path)
        self.assertEqual(len(blocks), 2)
        self.assertEqual([b["name"] for b in blocks], ["molA", "molB"])
        self.assertEqual([b["index"] for b in blocks], [0, 1])
        self.assertTrue(blocks[0]["lines"][-1].startswith("$$$$"))

    def test_empty_title_fallback(self):
        path = self.ws.write(
            "empty.sdf",
            "\n  OpenBabel\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\n$$$$\n",
        )
        blocks = sdf.parse_molecules(path)
        self.assertEqual(blocks[0]["name"], "mol_1")

    def test_write_molecule_roundtrip(self):
        path = self.ws.write(
            "one.sdf",
            "LIG\n  OpenBabel\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\n$$$$\n",
        )
        blocks = sdf.parse_molecules(path)
        out = os.path.join(self.ws.root, "out.sdf")
        sdf.write_molecule(out, blocks[0])
        again = sdf.parse_molecules(out)
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]["name"], "LIG")


if __name__ == "__main__":
    unittest.main()
