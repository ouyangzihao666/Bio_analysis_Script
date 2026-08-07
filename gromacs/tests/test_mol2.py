"""mol2 parsing / splitting tests."""

from __future__ import annotations

import os
import unittest

from mdkit import mol2
from mdkit.exceptions import ConfigError

from tests.helpers import MULTI_MOL2, TempWorkspace


class Mol2Tests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.path = self.ws.write("two.mol2", MULTI_MOL2)

    def tearDown(self):
        self.ws.cleanup()

    def test_parse_two_molecules(self):
        blocks = mol2.parse_molecules(self.path)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["name"], "obj03")
        self.assertEqual(blocks[1]["name"], "obj04")
        self.assertEqual(blocks[0]["substructures"], ["FME0"])
        self.assertEqual(blocks[1]["substructures"], ["BDO0"])

    def test_molecule_name_derived_from_substructure(self):
        blocks = mol2.parse_molecules(self.path)
        self.assertEqual(mol2.molecule_name(blocks[0]), "FME")
        self.assertEqual(mol2.molecule_name(blocks[1]), "BDO")

    def test_extract_molecule_by_index(self):
        out = os.path.join(self.ws.root, "one.mol2")
        block = mol2.extract_molecule(self.path, out, 1)
        self.assertEqual(block["name"], "obj04")
        parsed = mol2.parse_molecules(out)
        self.assertEqual(len(parsed), 1)

    def test_extract_molecule_by_name(self):
        out = os.path.join(self.ws.root, "one.mol2")
        mol2.extract_molecule(self.path, out, "FME")
        self.assertEqual(len(mol2.parse_molecules(out)), 1)

    def test_missing_molecule_raises(self):
        out = os.path.join(self.ws.root, "one.mol2")
        with self.assertRaises(ConfigError):
            mol2.extract_molecule(self.path, out, "NOPE")


if __name__ == "__main__":
    unittest.main()
