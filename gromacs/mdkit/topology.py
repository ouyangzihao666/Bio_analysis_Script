"""Topology (.top) helpers: include absolutization and [molecules] edits."""

from __future__ import annotations

import os
import re
from typing import List, Tuple

from mdkit.exceptions import ConfigError


_INCLUDE_QUOTED = re.compile(r'^#include\s+"([^"]+)"\s*$')
_MOLECULES_HEADER = re.compile(r"^\s*\[\s*molecules\s*\]\s*$")
_MOLECULETYPE_HEADER = re.compile(r"^\s*\[\s*moleculetype\s*\]\s*$")


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
    while insert_at < len(lines) and lines[insert_at].strip() and not lines[insert_at].strip().startswith(";"):
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
