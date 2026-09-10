#
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
#
"""Enforce link policies for README and documentation sources."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
DOCS = REPO_ROOT / "docs"

OWN_REPO_GITHUB = "https://github.com/verl-project/rl-insight/"
OWN_REPO_RAW = "https://raw.githubusercontent.com/verl-project/rl-insight/"
OWN_RTDOCS = "https://rl-insight.readthedocs.io/"

ALLOWED_REFS = {"main"}
VERSION_TAG = re.compile(r"v?\d+\.\d+\.\d+")

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
RST_LINK = re.compile(r"`[^`<]+\s*<([^`>]+)>`_")
HTML_HREF = re.compile(r'<a[^>]+href=["\']([^"\']+)["\']')
HTML_SRC = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']')


@dataclass(frozen=True)
class Link:
    file: Path
    line: int
    target: str
    kind: str


def iter_doc_files() -> Iterable[Path]:
    for path in DOCS.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".rst"}:
            yield path


def extract_links(path: Path) -> Iterable[Link]:
    text = path.read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        patterns = (
            (MARKDOWN_LINK, "markdown"),
            (RST_LINK, "rst"),
            (HTML_HREF, "html-a"),
            (HTML_SRC, "html-img"),
        )
        for pattern, kind in patterns:
            for match in pattern.finditer(line):
                is_markdown_image = (
                    pattern is MARKDOWN_LINK
                    and match.start() > 0
                    and line[match.start() - 1] == "!"
                )
                matched_kind = "markdown-image" if is_markdown_image else kind
                yield Link(path, line_no, match.group(1), matched_kind)


def is_relative(target: str) -> bool:
    return target.startswith(("./", "../"))


def is_own_repo_file_link(target: str) -> bool:
    if target.startswith(OWN_REPO_GITHUB):
        rest = target[len(OWN_REPO_GITHUB) :]
        return rest.startswith(("blob/", "tree/"))
    return target.startswith(OWN_REPO_RAW)


def is_own_rtdocs_link(target: str) -> bool:
    return target.startswith(OWN_RTDOCS)


def own_repo_ref(target: str) -> str | None:
    if target.startswith(OWN_REPO_GITHUB):
        rest = target[len(OWN_REPO_GITHUB) :]
        if rest.startswith(("blob/", "tree/")):
            return rest.split("/", 2)[1]
    if target.startswith(OWN_REPO_RAW):
        parts = target[len(OWN_REPO_RAW) :].split("/", 1)
        if parts:
            return parts[0]
    return None


def is_allowed_ref(ref: str) -> bool:
    return ref in ALLOWED_REFS or VERSION_TAG.fullmatch(ref) is not None


def location(link: Link) -> str:
    return f"{link.file.relative_to(REPO_ROOT)}:{link.line}"


def check_readme_no_relative_links() -> list[str]:
    errors: list[str] = []
    for link in extract_links(README):
        if is_relative(link.target):
            errors.append(
                f"{location(link)}: relative {link.kind} link is not allowed in README.md: {link.target}"
            )
    return errors


def check_docs_internal_links_are_relative() -> list[str]:
    errors: list[str] = []
    for path in iter_doc_files():
        for link in extract_links(path):
            if link.kind in {"html-img", "markdown-image"}:
                continue
            if is_own_repo_file_link(link.target) or is_own_rtdocs_link(link.target):
                errors.append(
                    f"{location(link)}: internal documentation link must be relative, not absolute: {link.target}"
                )
    return errors


def check_no_links_to_other_branches() -> list[str]:
    errors: list[str] = []
    files = [README, *iter_doc_files()]
    for path in files:
        for link in extract_links(path):
            ref = own_repo_ref(link.target)
            if ref is not None and not is_allowed_ref(ref):
                errors.append(
                    f"{location(link)}: absolute link references non-default branch '{ref}': {link.target}"
                )
    return errors


def main() -> int:
    errors = [
        *check_readme_no_relative_links(),
        *check_docs_internal_links_are_relative(),
        *check_no_links_to_other_branches(),
    ]
    if errors:
        print("Link policy violations:")
        for error in errors:
            print(f"  {error}")
        return 1
    print("OK: README and documentation links satisfy the repository policy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
