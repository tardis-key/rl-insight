# Copyright (c) 2026 verl-project authors.
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

"""Platform-specific fields attached to exported trace data."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Callable


_PlatformFields = Callable[[Mapping[str, str]], Mapping[str, str]]

_JD_FIELDS = ("a", "b", "c")


def _jd_fields(environ: Mapping[str, str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in _JD_FIELDS:
        value = environ.get(name.upper()) or environ.get(name)
        if value:
            fields[name] = value
    return fields


_PLATFORM_FIELDS: dict[str, _PlatformFields] = {
    "jd": _jd_fields,
}


def collect_platform_fields(
    platform: str | None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return platform fields to merge into every exported span.

    Values supplied by a caller take precedence over platform fields with the
    same key. Empty ``platform`` disables the mechanism. Unknown platforms raise
    so a typo cannot silently disable platform filtering.
    """
    if not platform:
        return {}
    provider = _PLATFORM_FIELDS.get(str(platform))
    if provider is None:
        supported = ", ".join(sorted(_PLATFORM_FIELDS))
        raise ValueError(
            f"Unknown monitor platform {platform!r}; supported: {supported}"
        )
    return dict(provider(os.environ if environ is None else environ))


__all__ = ["collect_platform_fields"]
