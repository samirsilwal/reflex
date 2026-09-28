"""Minimal JUnit XML reader, used only for shadow-mode reconciliation.

We deliberately compare at the *file* level, not exact test id: JUnit
`classname`/`file` attributes are formatted differently across pytest,
jest-junit, mocha-junit-reporter, and Playwright's junit reporter, and
trying to reconstruct each framework's exact TestUnit.id from it is
fragile. File-level is the granularity Laya already reasons at, so it's
the natural, robust comparison point: 'did any test in this file fail,
and did we select this file?'
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree as ET


def failed_files_from_junit(junit_path: str | Path) -> set[str]:
    """Return the set of file paths that had >=1 failing/erroring test case,
    best-effort across pytest/jest-junit/mocha-junit-reporter/playwright-junit
    XML shapes."""
    path = Path(junit_path)
    if not path.exists():
        return set()

    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return set()

    failed: set[str] = set()
    for testcase in tree.getroot().iter("testcase"):
        if testcase.find("failure") is None and testcase.find("error") is None:
            continue
        file_attr = testcase.get("file")
        if file_attr:
            failed.add(_normalize(file_attr))
            continue
        classname = testcase.get("classname", "")
        # pytest classname is a dotted module path (e.g. 'tests.test_billing');
        # jest-junit classname is often the describe-block/file name.
        guess = classname.replace(".", "/")
        if guess:
            failed.add(_normalize(guess))
    return failed


def _normalize(file_path: str) -> str:
    file_path = file_path.replace("\\", "/").lstrip("./")
    if not re.search(r"\.\w+$", file_path):
        file_path += ".py"  # dotted-module guesses need an extension back
    return file_path
