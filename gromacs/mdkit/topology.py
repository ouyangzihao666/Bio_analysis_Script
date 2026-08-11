"""Topology (.top) helpers: include absolutization and [molecules] edits."""

from __future__ import annotations

import os
import re
from typing import List, Tuple

from mdkit.exceptions import ConfigError


_INCLUDE_QUOTED = re.compile(r'^#include\s+"([^"]+)"\s*$')
_MOLECULES_HEADER = re.compile(r"^\s*\[\s*molecules\s*\]\s*$")
_MOLECULETYPE_HEADER = re.compile(r"^\s*\[\s*moleculetype\s*\]\s*$")
_SECTION_HEADER = re.compile(r"^\s*\[\s*[a-z0-9_]+\s*\]\s*$")


def absolutize_includes(src_top: str, dst_top: str) -> str:
    """Copy a .top file rewriting quoted includes to absolute paths.

    GROMACS resolves ``#include "x"`` relative to the including file's
    directory; absolutizing makes a copied topology self-contained.
    """
    src_dir = os.path.dirname(os.path.abspath(src_top))
    with open(src_top, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    out = []
    for line in lines:
        m = _INCLUDE_QUOTED.match(line)
        if m:
            inc = m.group(1)
            if os.path.isabs(inc):
                out.append(line)
            else:
                resolved = os.path.normpath(os.path.join(src_dir, inc))
                if os.path.exists(resolved):
                    out.append('#include "%s"' % resolved)
                else:
                    # Keep relative: GROMACS resolves it via the top file's
                    # directory or its own data/share directory (e.g. the
                    # force field dir, which pdb2gmx 2026 does not copy).
                    out.append(line)
        else:
            out.append(line)
    os.makedirs(os.path.dirname(os.path.abspath(dst_top)), exist_ok=True)
    with open(dst_top, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return dst_top


def insert_includes_after_first(top_path: str, include_paths: List[str]) -> None:
    """Insert ``#include`` lines right after the first include in the file."""
    with open(top_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    insert_at = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#include"):
            insert_at = i
            break
    if insert_at is None:
        raise ConfigError("拓扑文件中没有 #include 行: %s" % top_path)
    new_lines = []
    for inc in include_paths:
        new_lines.append('#include "%s"' % os.path.abspath(inc))
    lines[insert_at + 1 : insert_at + 1] = new_lines
    with open(top_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def append_molecules(top_path: str, entries: List[Tuple[str, int]]) -> None:
    """Append ``name count`` lines to the [molecules] section."""
    with open(top_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if _MOLECULES_HEADER.match(line):
            header_idx = i
            break
    if header_idx is None:
        lines.append("")
        lines.append("[ molecules ]")
        header_idx = len(lines) - 1
    insert_at = header_idx + 1
    # Skip existing entries, comments and blank lines (pdb2gmx 2026 writes a
    # "; Compound #mols" comment before the first molecule) so the appended
    # molecules land after the current entries. The [molecules] order must
    # match the structure order (protein first, then ligands).
    while insert_at < len(lines):
        line = lines[insert_at].strip()
        if not line or line.startswith(";"):
            insert_at += 1
            continue
        if line.startswith("["):
            break
        insert_at += 1
    new_lines = ["%-12s %d" % (name, count) for name, count in entries]
    lines[insert_at:insert_at] = new_lines
    with open(top_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def rename_molecule(itp_path: str, gro_path: str, new_name: str) -> None:
    """Rename the molecule in an acpype-style itp and its matching gro.

    Ensures [molecules] entries in the complex topology match the
    moleculetype name in the itp and the residue name in the gro.
    """
    with open(itp_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    renamed = False
    for i, line in enumerate(lines):
        if _MOLECULETYPE_HEADER.match(line):
            j = i + 1
            while j < len(lines) and (
                not lines[j].strip() or lines[j].strip().startswith(";")
            ):
                j += 1
            if j < len(lines):
                line = lines[j]
                fields = line.split()
                if fields:
                    indent_len = len(line) - len(line.lstrip())
                    lines[j] = (
                        line[:indent_len]
                        + new_name
                        + line[indent_len + len(fields[0]) :]
                    )
                    renamed = True
            break
    if not renamed:
        raise ConfigError("itp 中未找到 [ moleculetype ] 名称: %s" % itp_path)
    with open(itp_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    # Rewrite the residue name column in the gro (cols 5..9).
    with open(gro_path, "r", encoding="utf-8", errors="replace") as fh:
        gro_lines = fh.read().splitlines()
    if len(gro_lines) >= 3:
        try:
            natoms = int(gro_lines[1].strip())
        except ValueError:
            natoms = 0
        for idx in range(2, min(2 + natoms, len(gro_lines))):
            line = gro_lines[idx]
            gro_lines[idx] = line[:5] + new_name.ljust(5)[:5] + line[10:]
    with open(gro_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(gro_lines) + "\n")


def merge_ligand_itps(itp_paths, ligand_names, out_path: str) -> None:
    """Merge acpype-style ligand itps into one topology include.

    GROMACS only allows a single [ atomtypes ] section, so atom types from
    the first ligand are kept and duplicated sections are stripped from the
    rest. Duplicate type names with differing parameters raise ConfigError.
    """
    if len(itp_paths) == 1:
        import shutil

        shutil.copyfile(itp_paths[0], out_path)
        return
    seen_types = {}
    atomtype_lines = []
    body_blocks = []
    for idx, path in enumerate(itp_paths):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        in_atomtypes = False
        kept = []
        for line in lines:
            if _SECTION_HEADER.match(line):
                if line.strip().lower() == "[ atomtypes ]":
                    in_atomtypes = True
                    if not atomtype_lines:
                        atomtype_lines.append(line)
                    continue
                in_atomtypes = False
                kept.append(line)
                continue
            if in_atomtypes:
                stripped = line.strip()
                if stripped and not stripped.startswith(";"):
                    fields = stripped.split()
                    if len(fields) >= 2:
                        tname = fields[0]
                        if tname in seen_types and seen_types[tname] != stripped:
                            raise ConfigError(
                                "配体 %s 与之前的配体定义了不同的原子类型 %s，"
                                "请检查 GAFF 原子类型冲突" % (ligand_names[idx], tname)
                            )
                        if tname not in seen_types:
                            seen_types[tname] = stripped
                            atomtype_lines.append(line)
            else:
                kept.append(line)
        body_blocks.append(kept)
    out_lines = list(atomtype_lines)
    for block in body_blocks:
        if out_lines and block:
            out_lines.append("")
        out_lines.extend(block)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out_lines) + "\n")


def count_components(itp_path: str):
    """Count disconnected molecular fragments in an acpype-style itp.

    A merged multi-molecule input (e.g. two ligands under the same residue
    name) produces one moleculetype with several disconnected fragments.
    Returns (natoms, n_components).
    """
    with open(itp_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    section = None
    natoms = 0
    bonds = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped[1:].strip().rstrip("]").strip().lower()
            continue
        if not stripped or stripped.startswith(";"):
            continue
        fields = stripped.split()
        if section == "atoms":
            natoms += 1
        elif section == "bonds":
            if len(fields) >= 3:
                try:
                    bonds.append((int(fields[0]), int(fields[1])))
                except ValueError:
                    pass
    if natoms == 0:
        return 0, 0
    parent = list(range(natoms))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in bonds:
        if 1 <= a <= natoms and 1 <= b <= natoms:
            ra, rb = find(a - 1), find(b - 1)
            if ra != rb:
                parent[ra] = rb
    n_components = len({find(i) for i in range(natoms)})
    return natoms, n_components
