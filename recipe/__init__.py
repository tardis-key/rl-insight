# Copyright (c) 2025 verl-project authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Cluster scheduling analysis and visualization for RL workloads.

Recipe dependencies are optional and loaded only when recipe functionality is used.
"""

from importlib import import_module
from typing import Any


_EXPORTS = {
    "MstxClusterParser": ".parser",
    "TorchClusterParser": ".parser",
    "NvtxClusterParser": ".parser",
}

_RECIPE_DEPENDENCY_MODULES = {
    "ijson",
    "kaleido",
    "loguru",
    "matplotlib",
    "numpy",
    "pandas",
    "plotly",
    "torch",
}
_RECIPE_INSTALL_HINT = 'pip install "rl-insight[recipe]"'


def _is_missing_recipe_dependency(exc: ModuleNotFoundError) -> bool:
    module_name = (exc.name or "").partition(".")[0]
    return module_name in _RECIPE_DEPENDENCY_MODULES


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        value = getattr(import_module(module_name, __name__), name)
    except ModuleNotFoundError as exc:
        if _is_missing_recipe_dependency(exc):
            raise ImportError(
                f"Failed to import {name} because Recipe dependencies are not "
                f"installed. Install them with: {_RECIPE_INSTALL_HINT}"
            ) from exc
        raise
    globals()[name] = value
    return value


def main():
    try:
        from .main import main as _main
    except ModuleNotFoundError as exc:
        if _is_missing_recipe_dependency(exc):
            raise SystemExit(
                "Recipe dependencies are not installed. "
                f"Install them with: {_RECIPE_INSTALL_HINT}"
            ) from exc
        raise

    return _main()


__all__ = [
    "MstxClusterParser",
    "TorchClusterParser",
    "NvtxClusterParser",
    "main",
]
