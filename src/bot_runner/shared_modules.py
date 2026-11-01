"""The `shared` package: modules written in the orchestrator UI, importable by handlers.

``from shared import downloads`` or ``import shared.downloads`` loads the code the
orchestrator sent with the job. Modules run only when imported, and their source is
registered with linecache so tracebacks show the failing lines.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import linecache
import sys
import types
from typing import Any

PACKAGE = "shared"


def register_source(filename: str, code: str) -> None:
    """Let tracebacks show lines of code that was compiled from a string."""
    lines = [line + "\n" for line in code.splitlines()]
    linecache.cache[filename] = (len(code), None, lines, filename)


class _Loader(importlib.abc.Loader):
    def __init__(self, name: str, code: str, version: int):
        self._name = name
        self._code = code
        self._filename = f"<{PACKAGE}.{name} v{version}>"

    def create_module(self, spec):
        return None  # default module creation

    def exec_module(self, module: types.ModuleType) -> None:
        register_source(self._filename, self._code)
        module.__file__ = self._filename
        exec(compile(self._code, self._filename, "exec"), module.__dict__)  # noqa: S102


class _Finder(importlib.abc.MetaPathFinder):
    def __init__(self, modules: dict[str, dict[str, Any]]):
        self._modules = modules

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname == PACKAGE:
            spec = importlib.machinery.ModuleSpec(PACKAGE, None, is_package=True)
            spec.submodule_search_locations = []
            return spec
        prefix, _, name = fullname.partition(".")
        if prefix != PACKAGE or "." in name or name not in self._modules:
            return None
        module = self._modules[name]
        loader = _Loader(name, module["code"], int(module.get("version", 0)))
        return importlib.machinery.ModuleSpec(fullname, loader, origin=f"<{fullname}>")


class _PackageLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        module.__doc__ = "Shared modules from the orchestrator."


def install(modules: dict[str, dict[str, Any]]) -> None:
    """Make `shared.<name>` importable for the given {name: {version, code}}."""
    finder = _Finder(modules)
    sys.meta_path.insert(0, finder)
    spec = finder.find_spec(PACKAGE)
    spec.loader = _PackageLoader()
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    sys.modules[PACKAGE] = package
