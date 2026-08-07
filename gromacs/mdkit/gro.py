"""GRO and PDB parsing/merging plus custom index generation."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from mdkit.exceptions import ConfigError


WATER_RES = {"SOL", "HOH", "W", "TIP3", "TIP4", "TIP5", "SPCE", "H2O"}
BACKBONE_ATOMS = {"N", "CA", "C", "O"}


def read_gro(path: str) -> Dict:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    if len(lines) < 3:
        raise ConfigError("GRO 文件格式错误: %s" % path)
    try:
        natoms = int(lines[1].strip())
    except ValueError:
        raise ConfigError("GRO 文件原子数行无效: %s" % path)
    atoms = []
    for line in lines[2 : 2 + natoms]:
        atoms.append(parse_gro_atom(line))
    box = lines[2 + natoms] if len(lines) > 2 + natoms else ""
    return {"title": lines[0], "natoms": natoms, "atoms": atoms, "box": box}


def parse_gro_atom(line: str) -> Dict:
    try:
        return {
            "resnum": int(line[0:5]),
            "resname": line[5:10].strip(),
            "atomname": line[10:15].strip(),
            "atomnum": int(line[15:20]),
            "x": float(line[20:28]),
            "y": float(line[28:36]),
            "z": float(line[36:44]),
        }
    except (ValueError, IndexError) as exc:
        raise ConfigError("GRO 原子行格式错误: %r（%s）" % (line, exc))


def format_gro_atom(resnum: int, resname: str, atomname: str, atomnum: int, x, y, z) -> str:
    return "%5d%-5s%5s%5d%8.3f%8.3f%8.3f" % (
        resnum,
        resname[:5],
        atomname[:5],
        atomnum,
        float(x),
        float(y),
        float(z),
    )


def merge_gro(protein_gro: str, ligand_gros: List[str], out_path: str, title: str) -> Dict:
    """Concatenate protein and ligand GRO structures into one file."""
    prot = read_gro(protein_gro)
    atoms = list(prot["atoms"])
    ligand_atom_counts = []
    for lg in ligand_gros:
        lig = read_gro(lg)
        ligand_atom_counts.append(lig["natoms"])
        atoms.extend(lig["atoms"])
    total = len(atoms)
    lines = [title, str(total)]
    for i, a in enumerate(atoms, 1):
        lines.append(
            format_gro_atom(a["resnum"], a["resname"], a["atomname"], i, a["x"], a["y"], a["z"])
        )
    lines.append(prot["box"])
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return {"natoms": total, "protein_atoms": prot["natoms"], "ligand_atom_counts": ligand_atom_counts}


def build_index(
    structure_gro: str,
    protein_atoms: int,
    ligand_atom_counts: List[int],
    ligand_names: List[str],
    out_path: str,
) -> int:
    """Write a custom .ndx with protein / ligand / water / ion groups.

    Returns total atom count.
    """
    data = read_gro(structure_gro)
    natoms = data["natoms"]
    if protein_atoms + sum(ligand_atom_counts) > natoms:
        raise ConfigError(
            "索引生成失败：蛋白+配体原子数(%d) 超过结构原子数(%d)"
            % (protein_atoms + sum(ligand_atom_counts), natoms)
        )
    ligand_start = protein_atoms + 1
    ligand_ranges = []
    cursor = ligand_start
    for count in ligand_atom_counts:
        ligand_ranges.append((cursor, cursor + count - 1))
        cursor += count

    groups = {
        "System": list(range(1, natoms + 1)),
        "Protein": list(range(1, protein_atoms + 1)),
        "C-alpha": [],
        "Backbone": [],
        "non-Protein": list(range(ligand_start, natoms + 1)),
        "Water": [],
        "SOL": [],
        "Ion": [],
        "Ligand": [],
    }
    for i, (start, end) in enumerate(ligand_ranges):
        groups["Ligand_%s" % ligand_names[i]] = list(range(start, end + 1))
        groups["Ligand"].extend(range(start, end + 1))

    for a in data["atoms"][:protein_atoms]:
        idx = a["atomnum"]
        if a["atomname"] == "CA":
            groups["C-alpha"].append(idx)
        if a["atomname"] in BACKBONE_ATOMS:
            groups["Backbone"].append(idx)
    for a in data["atoms"][protein_atoms:]:
        idx = a["atomnum"]
        resname = a["resname"].upper()
        if resname in WATER_RES:
            groups["Water"].append(idx)
            groups["SOL"].append(idx)
        else:
            groups["Ion"].append(idx)

    groups["Protein_Ligand"] = sorted(groups["Protein"] + groups["Ligand"])
    for name in ligand_names:
        groups["Protein_Ligand_%s" % name] = sorted(
            groups["Protein"] + groups["Ligand_%s" % name]
        )

    with open(out_path, "w", encoding="utf-8") as fh:
        for gname in sorted(groups):
            idxs = groups[gname]
            if not idxs:
                continue
            fh.write("[ %s ]\n" % gname)
            for i in range(0, len(idxs), 16):
                fh.write(" ".join("%6d" % v for v in idxs[i : i + 16]) + "\n")
    return natoms


def merge_pdb_chains(
    chain_paths: List[str],
    out_path: str,
    remove_water: bool = True,
    remove_residues: Optional[List[str]] = None,
) -> int:
    """Concatenate PDB chains into one multi-chain PDB for pdb2gmx."""
    remove_residues = {r.upper() for r in (remove_residues or [])}
    out_atoms = []
    chains = []
    for path in chain_paths:
        if not os.path.isfile(path):
            raise ConfigError("蛋白链文件不存在: %s" % path)
        chain_atoms = []
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not (line.startswith("ATOM") or line.startswith("HETATM")):
                    continue
                resname = line[17:20].strip().upper()
                if remove_water and resname in WATER_RES:
                    continue
                if resname in remove_residues:
                    continue
                chain_atoms.append(_parse_pdb_atom(line))
        if not chain_atoms:
            raise ConfigError("蛋白链文件中没有 ATOM 记录: %s" % path)
        chains.append(chain_atoms)
    atom_no = 0
    with open(out_path, "w", encoding="utf-8") as fh:
        for ci, chain_atoms in enumerate(chains):
            res_no = 0
            prev_res = None
            chain_id = chain_atoms[0].get("chain", "A" if ci == 0 else chr(65 + ci))
            for a in chain_atoms:
                atom_no += 1
                if a.get("resseq") != prev_res:
                    res_no += 1
                    prev_res = a.get("resseq")
                fh.write(
                    "ATOM  %5d %-4s%1s%3s %1s%4d%1s   %8.3f%8.3f%8.3f%6.2f%6.2f\n"
                    % (
                        atom_no,
                        a["name"][:4],
                        a.get("altloc", " "),
                        a["resname"][:3],
                        chain_id[:1],
                        res_no,
                        a.get("icode", " "),
                        a["x"],
                        a["y"],
                        a["z"],
                        a.get("occ", 1.0),
                        a.get("bfac", 0.0),
                    )
                )
            fh.write("TER\n")
        fh.write("END\n")
    return atom_no


def extract_pdb_residue(
    pdb_path: str,
    resname: str,
    out_path: str,
    ligand_name: Optional[str] = None,
) -> int:
    """Extract all atoms of one PDB residue into a single-ligand PDB."""
    atoms = []
    with open(pdb_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith(("ATOM", "HETATM")) and line[17:20].strip().upper() == resname.upper():
                atoms.append(_parse_pdb_atom(line))
    if not atoms:
        raise ConfigError(
            "PDB 中未找到残基 %s 的原子: %s" % (resname, pdb_path)
        )
    out_resname = (ligand_name or resname)[:3]
    with open(out_path, "w", encoding="utf-8") as fh:
        for i, a in enumerate(atoms, 1):
            fh.write(
                "HETATM%5d %-4s%1s%3s %1s%4d%1s   %8.3f%8.3f%8.3f%6.2f%6.2f\n"
                % (
                    i,
                    a["name"][:4],
                    a.get("altloc", " "),
                    out_resname,
                    " ",
                    1,
                    a.get("icode", " "),
                    a["x"],
                    a["y"],
                    a["z"],
                    a.get("occ", 1.0),
                    a.get("bfac", 0.0),
                )
            )
        fh.write("END\n")
    return len(atoms)


def count_pdb_residue_components(pdb_path: str, resname: str) -> int:
    """Count connected fragments of one PDB residue using CONECT records."""
    serials = []
    with open(pdb_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith(("ATOM", "HETATM")) and line[17:20].strip().upper() == resname.upper():
                try:
                    serials.append(int(line[6:11]))
                except ValueError:
                    pass
    if not serials:
        raise ConfigError(
            "PDB 中未找到残基 %s 的原子: %s" % (resname, pdb_path)
        )
    serial_set = set(serials)
    idx = {s: i for i, s in enumerate(serials)}
    parent = list(range(len(serials)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    with open(pdb_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith("CONECT"):
                continue
            try:
                fields = [int(line[i : i + 5]) for i in range(6, len(line.rstrip()), 5)]
            except ValueError:
                continue
            if not fields:
                continue
            if fields[0] not in serial_set:
                continue
            for other in fields[1:]:
                if other in serial_set:
                    a, b = idx[fields[0]], idx[other]
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[ra] = rb
    return len({find(i) for i in range(len(serials))})


def _parse_pdb_atom(line: str) -> Dict:
    try:
        return {
            "name": line[12:16].strip(),
            "altloc": line[16],
            "resname": line[17:20].strip(),
            "chain": line[21].strip() or " ",
            "resseq": line[22:26].strip(),
            "icode": line[26].strip() or " ",
            "x": float(line[30:38]),
            "y": float(line[38:46]),
            "z": float(line[46:54]),
            "occ": float(line[54:60] or 1.0),
            "bfac": float(line[60:66] or 0.0),
        }
    except (ValueError, IndexError) as exc:
        raise ConfigError("PDB 原子行格式错误: %r（%s）" % (line.rstrip(), exc))
