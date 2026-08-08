"""Option-level merging for external CLI args (e.g. gmx mdrun).

GROMACS rejects duplicate options, so mdkit assembles a single
deduplicated argv from structured params, user extra args and slot args.
Later occurrences of the same option override earlier ones (last wins).
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def parse_options(argv: List[str]) -> List[Tuple[str, Optional[str]]]:
    """Tokenize args into (option, value) pairs, preserving order.

    ``-flag value`` -> ("-flag", "value"); ``-flag`` (no following non-dash
    token) -> ("-flag", None). Positional tokens are kept as flags.
    """
    opts: List[Tuple[str, Optional[str]]] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("-") and len(tok) > 1:
            name = tok
            value = None
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            opts.append((name, value))
        else:
            opts.append((tok, None))
            i += 1
    return opts


def merge_cli_options(*arg_lists: List[str]) -> List[str]:
    """Merge option lists, deduplicating by option name (last wins)."""
    merged: dict = {}
    order: List[str] = []
    for argv in arg_lists:
        for name, value in parse_options(argv):
            if name not in merged:
                order.append(name)
            merged[name] = value
    out: List[str] = []
    for name in order:
        out.append(name)
        if merged[name] is not None:
            out.append(merged[name])
    return out
