"""mdp template resolution and YAML-override rendering."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Dict, Optional

from mdkit.exceptions import ConfigError


_KEY_LINE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_template(spec: str, mdp_dir: str) -> str:
    """Resolve an mdp spec to a template file path.

    ``spec`` may be a builtin name (ions/minim/nvt/npt/md) resolved inside
    ``mdp_dir``, or an explicit ``.mdp`` file path (absolute, or relative to
    the caller's cwd).
    """
    if spec.endswith(".mdp") or "/" in spec or "\\" in spec:
        p = os.path.expanduser(spec)
        if not os.path.isabs(p):
            p = os.path.abspath(p)
        if not os.path.isfile(p):
            raise ConfigError("mdp 文件不存在: %s" % p)
        return p
    p = os.path.join(mdp_dir, spec + ".mdp")
    if not os.path.isfile(p):
        raise ConfigError("内置 mdp 模板不存在: %s（mdp_dir=%s）" % (spec, mdp_dir))
    return p


def render_mdp(template_path: str, overrides: Optional[dict], out_path: str) -> dict:
    """Render a template plus overrides into ``out_path``.

    Returns metadata dict: {template, template_sha256, overrides}.
    Never modifies the template itself.
    """
    with open(template_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    lines = text.splitlines()
    overrides = dict(overrides or {})
    new_lines = []
    for line in lines:
        m = _KEY_LINE.match(line)
        if m and m.group(1) in overrides:
            key = m.group(1)
            indent = line[: m.start()]
            new_lines.append(
                "%s%s = %s" % (indent, key, _format_value(overrides.pop(key)))
            )
        else:
            new_lines.append(line)
    for key, value in overrides.items():
        new_lines.append("%s = %s" % (key, _format_value(value)))
    rendered = "\n".join(new_lines) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(rendered)
    return {
        "template": os.path.abspath(template_path),
        "template_sha256": sha256_text(text),
        "overrides": {k: _format_value(v) for k, v in overrides.items()},
    }
