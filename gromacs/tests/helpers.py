"""Shared test helpers: fake gmx, tiny structures, temp configs."""

from __future__ import annotations

import os
import shutil
import tempfile


FAKE_GMX = r'''#!/usr/bin/env python3
import os, sys

def touch(p):
    if p:
        open(p, "w").close()

def gro_lines(natoms, resname, start_no=1, first=1):
    lines = ["fake gmx", str(natoms)]
    for i in range(natoms):
        no = first + i
        lines.append(
            "%5d%-5s%5s%5d%8.3f%8.3f%8.3f"
            % (1, resname, ("A%d" % no)[:5], no, 1.0, 1.0, 1.0)
        )
    lines.append("   2.000   2.000   2.000")
    return "\n".join(lines) + "\n"

PROTEIN_GRO = gro_lines(8, "PROT")
SYSTEM_GRO = gro_lines(20, "SOL")
TOP = '#include "forcefield.itp"\n\n[ molecules ]\nProtein 1\n'

args = sys.argv[1:]
if args and args[0] == "--version":
    print("GROMACS version:    2024.1")
    sys.exit(0)
if not args:
    sys.exit(2)
sub = args[0]
fail = os.environ.get("FAKE_GMX_FAIL", "")
if sub in [f.strip() for f in fail.split(",") if f.strip()] and sub != "mdrun":
    sys.stderr.write("fake gmx failure for %s\n" % sub)
    sys.exit(7)

def val(key):
    for i, a in enumerate(args):
        if a == key and i + 1 < len(args):
            return args[i + 1]
    return None

if sub == "pdb2gmx":
    open(val("-o"), "w").write(PROTEIN_GRO)
    open(val("-p"), "w").write(TOP)
elif sub in ("editconf", "trjconv", "rms", "rmsf", "gyrate", "hbond", "dssp"):
    src = val("-f") or val("-cp")
    if val("-o") and src and os.path.isfile(src):
        open(val("-o"), "w").write(open(src).read())
    else:
        touch(val("-o"))
elif sub == "solvate":
    src = val("-cp")
    if src and os.path.isfile(src):
        open(val("-o"), "w").write(open(src).read())
    else:
        open(val("-o"), "w").write(SYSTEM_GRO)
    top = val("-p")
    if top:
        with open(top, "a") as fh:
            fh.write("SOL 1\n")
elif sub == "grompp":
    touch(val("-o"))
elif sub == "genion":
    open(val("-o"), "w").write(SYSTEM_GRO)
    top = val("-p")
    if top:
        with open(top, "a") as fh:
            fh.write("NA 1\n")
elif sub == "mdrun":
    name = val("-deffnm")
    if name and name.endswith("_md") and "mdrun" in fail.split(","):
        sys.stderr.write("fake gmx failure for mdrun\n")
        sys.exit(7)
    if os.environ.get("FAKE_GMX_SLOW_MD") and name and name.endswith("_md"):
        import time

        for i in range(10):
            print(
                "step %d, remaining wall clock time: %d s"
                % ((i + 1) * 100, 1000 - i * 100),
                flush=True,
            )
            time.sleep(0.2)
    if name:
        for ext in ("gro", "edr", "log", "cpt", "xtc"):
            open(name + "." + ext, "w").write(SYSTEM_GRO if ext == "gro" else "x")
sys.exit(0)
'''


FAKE_LIGAND_TOOLS = r'''#!/usr/bin/env python3
import os, sys

def val(key):
    for i, a in enumerate(sys.argv):
        if a == key and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None

tool = os.path.basename(sys.argv[0])
if tool == "obabel":
    out = val("-O")
    if out:
        open(out, "w").write("obabel fake\n")
elif tool == "antechamber":
    out = val("-o")
    if out:
        open(out, "w").write("antechamber fake\n")
elif tool == "acpype":
    inp = val("-i")
    if not inp:
        sys.exit(1)
    stem = os.path.splitext(os.path.basename(inp))[0]
    d = stem + ".acpype"
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, stem + "_GMX.itp"), "w") as fh:
        fh.write("[ moleculetype ]\n; name  nrexcl\nLIG 3\n")
    with open(os.path.join(d, stem + "_GMX.gro"), "w") as fh:
        fh.write("lig\n    1\n    1LIG      C    1   1.000   1.000   1.000\n   2.000   2.000   2.000\n")
sys.exit(0)
'''


def make_fake_gmx() -> str:
    """Create a fake gmx binary in a temp dir; returns the dir path."""
    d = tempfile.mkdtemp(prefix="mdkit_fakebin_")
    path = os.path.join(d, "gmx")
    with open(path, "w") as fh:
        fh.write(FAKE_GMX)
    os.chmod(path, 0o755)
    return d


def make_fake_ligand_tools() -> str:
    """Fake obabel / antechamber / acpype binaries; returns dir path."""
    d = tempfile.mkdtemp(prefix="mdkit_fakelig_")
    for tool in ("obabel", "antechamber", "parmchk2", "acpype"):
        path = os.path.join(d, tool)
        with open(path, "w") as fh:
            fh.write(FAKE_LIGAND_TOOLS)
        os.chmod(path, 0o755)
    return d


def with_fake_path(fake_bin: str, env=None):
    env = dict(env or os.environ)
    env["PATH"] = fake_bin + os.pathsep + env.get("PATH", "")
    return env


TINY_PDB = (
    "ATOM      1  N   MET A   1      11.608  14.140  11.080  1.00  0.00           N\n"
    "ATOM      2  CA  MET A   1      12.122  13.469  12.258  1.00  0.00           C\n"
    "ATOM      3  C   MET A   1      13.455  14.165  12.538  1.00  0.00           C\n"
    "ATOM      4  O   MET A   1      13.544  15.331  12.918  1.00  0.00           O\n"
    "END\n"
)

TINY_GRO = """tiny protein
2
    1PROT     N    1   1.000   1.000   1.000
    1PROT    CA    2   1.001   1.001   1.001
   2.000   2.000   2.000
"""

TINY_LIG_GRO = """tiny ligand
2
    1LIG      C    1   2.100   2.100   2.100
    1LIG      H    2   2.200   2.200   2.200
   2.000   2.000   2.000
"""

MULTI_MOL2 = """# created with PyMOL
@<TRIPOS>MOLECULE
obj03
13 13 1
SMALL
USER_CHARGES
@<TRIPOS>ATOM
1\tO\t4.944\t-15.548\t4.027\tO.2\t1\tFME0\t0.000
2\tC\t5.095\t-14.458\t4.825\tC.2\t1\tFME0\t0.000
@<TRIPOS>BOND
1\t1\t2\t1
@<TRIPOS>MOLECULE
obj04
8 7 1
SMALL
USER_CHARGES
@<TRIPOS>ATOM
1\tC\t2.661\t-7.976\t3.560\tC.3\t1\tBDO0\t0.000
2\tC\t3.059\t-6.942\t2.514\tC.3\t1\tBDO0\t0.000
@<TRIPOS>BOND
1\t1\t2\t1
"""


def write(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


class TempWorkspace:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="mdkit_test_")
        self.inputs = os.path.join(self.root, "inputs")
        os.makedirs(self.inputs, exist_ok=True)

    def write(self, rel: str, content: str) -> str:
        return write(os.path.join(self.root, rel), content)

    def add_protein(self, name="protein_A.pdb") -> str:
        return self.write("inputs/" + name, TINY_PDB)

    def add_ligand(self, name="ligand_1.sdf") -> str:
        return self.write(
            "inputs/" + name,
            "ligand\n 1\n    1LIG  C    1   0.000   0.000   0.000\n",
        )

    def systems_yaml(self, systems_block: str) -> str:
        return self.write(
            "systems.yaml",
            "work_dir: ./result\nsystems:\n%s\n" % systems_block,
        )

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
