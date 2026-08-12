"""GRO merge, PDB chain merge and index generation tests."""

from __future__ import annotations

import os
import unittest

from mdkit import gro

from tests.helpers import TINY_GRO, TINY_LIG_GRO, TINY_PDB, TempWorkspace


class GroTests(unittest.TestCase):
    def setUp(self):
        self.ws = TempWorkspace()
        self.protein = self.ws.write("protein.gro", TINY_GRO)
        self.lig = self.ws.write("lig.gro", TINY_LIG_GRO)

    def tearDown(self):
        self.ws.cleanup()

    def test_merge_gro(self):
        out = os.path.join(self.ws.root, "complex.gro")
        info = gro.merge_gro(self.protein, [self.lig], out, "complex")
        self.assertEqual(info["natoms"], 4)
        self.assertEqual(info["protein_atoms"], 2)
        self.assertEqual(info["ligand_atom_counts"], [2])
        data = gro.read_gro(out)
        self.assertEqual(data["natoms"], 4)
        self.assertIn("2.000", data["box"])

    def test_build_index(self):
        atoms = [
            (1, "PROT", "N", 1),
            (1, "PROT", "CA", 2),
            (1, "LIG", "C", 3),
            (1, "LIG", "H", 4),
            (1, "SOL", "O", 5),
            (1, "SOL", "H", 6),
            (1, "NA", "NA", 7),
            (1, "CL", "CL", 8),
        ]
        lines = ["solvated", "8"]
        for resnum, resname, atomname, atomnum in atoms:
            lines.append(
                gro.format_gro_atom(resnum, resname, atomname, atomnum, 0.0, 0.0, 0.0)
            )
        lines.append("   2.000   2.000   2.000")
        solvated = self.ws.write("solv.gro", "\n".join(lines) + "\n")
        out = os.path.join(self.ws.root, "system.ndx")
        n = gro.build_index(
            solvated,
            protein_atoms=2,
            ligand_atom_counts=[2],
            ligand_names=["LIG"],
            out_path=out,
            ion_names=("NA", "CL"),
        )
        self.assertEqual(n, 8)
        with open(out, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("[ Protein ]", content)
        self.assertIn("[ Ligand_LIG ]", content)
        self.assertIn("[ C-alpha ]", content)
        self.assertIn("[ Ion ]", content)
        self.assertIn("[ Protein_Ligand_LIG ]", content)
        # Ion 组只含显式命名的阴阳离子（不含配体原子 3、4）
        ion_block = content.split("[ Ion ]")[1].split("[")[0]
        self.assertNotIn("3", ion_block.split())
        self.assertNotIn("4", ion_block.split())
        self.assertIn("7", ion_block.split())
        self.assertIn("8", ion_block.split())

    def test_merge_pdb_chains(self):
        a = self.ws.write("a.pdb", _pdb_chain("A"))
        b = self.ws.write("b.pdb", _pdb_chain("B"))
        out = os.path.join(self.ws.root, "merged.pdb")
        n = gro.merge_pdb_chains([a, b], out, remove_water=True)
        self.assertEqual(n, 8)
        with open(out, encoding="utf-8") as fh:
            content = fh.read()
        self.assertEqual(content.count("ATOM"), 8)
        self.assertIn("TER", content)

    def test_count_pdb_residue_components(self):
        def hetatm(serial, name, resname, x, y, z):
            return "HETATM%5d %-4s%1s%3s %1s%4d%1s   %8.3f%8.3f%8.3f%6.2f%6.2f" % (
                serial, name, " ", resname, " ", 1, " ", x, y, z, 0.0, 0.0,
            )

        pdb = self.ws.write(
            "mix.pdb",
            "\n".join(
                [
                    hetatm(101, "C", "UNK", 1.0, 1.0, 1.0),
                    hetatm(102, "C", "UNK", 1.1, 1.0, 1.0),
                    hetatm(103, "O", "UNK", 2.0, 2.0, 2.0),
                    hetatm(104, "O", "UNK", 2.1, 2.0, 2.0),
                    "CONECT 101 102",
                    "CONECT 103 104",
                    "END",
                ]
            )
            + "\n",
        )
        self.assertEqual(gro.count_pdb_residue_components(pdb, "UNK"), 2)


def _pdb_chain(chain_id: str) -> str:
    lines = []
    for i, line in enumerate(TINY_PDB.splitlines(), 1):
        if line.startswith("ATOM"):
            line = line[:21] + chain_id + line[22:]
            line = line[:6] + "%5d" % i + line[11:]
        lines.append(line)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    unittest.main()
