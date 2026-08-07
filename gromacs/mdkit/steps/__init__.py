"""Step registry: auto-discover built-in and external steps."""

from __future__ import annotations

import importlib.util
import inspect
import os
import pkgutil
from typing import Dict, Optional

from mdkit.exceptions import ConfigError
from mdkit.steps.base import Step


def _iter_step_classes(module):
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if (
            issubclass(obj, Step)
            and obj is not Step
            and getattr(obj, "name", "")
        ):
            yield obj


def load_builtin_steps() -> Dict[str, Step]:
    steps = {}
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    for mod_info in pkgutil.iter_modules([pkg_dir]):
        if mod_info.name == "base":
            continue
        spec = importlib.util.spec_from_file_location(
            "mdkit.steps." + mod_info.name,
            os.path.join(pkg_dir, mod_info.name + ".py"),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for cls in _iter_step_classes(module):
            instance = cls()
            steps[instance.name] = instance
    return steps


def load_external_steps(steps_dir: Optional[str]) -> Dict[str, Step]:
    steps = {}
    if not steps_dir:
        return steps
    if not os.path.isdir(steps_dir):
        raise ConfigError("外部步骤目录不存在: %s" % steps_dir)
    for fname in sorted(os.listdir(steps_dir)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        path = os.path.join(steps_dir, fname)
        module_name = "mdkit_external_" + fname[:-3]
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for cls in _iter_step_classes(module):
            instance = cls()
            steps[instance.name] = instance
    return steps


def load_steps(steps_dir: Optional[str] = None) -> Dict[str, Step]:
    steps = load_builtin_steps()
    external = load_external_steps(steps_dir)
    for name, step in external.items():
        steps[name] = step
    return steps
