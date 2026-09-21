#!/usr/bin/env python3
"""Dependency-free quality gate used locally and by CI."""

import ast
import json
import py_compile
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_FILES = sorted(path for path in ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


def main():
    errors = 0
    for path in PYTHON_FILES:
        try:
            py_compile.compile(str(path), doraise=True)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, py_compile.PyCompileError) as exc:
            errors += fail(f"{path.relative_to(ROOT)}: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"run", "Popen", "call", "check_call", "check_output"}:
                    for keyword in node.keywords:
                        if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                            errors += fail(f"shell=True forbidden: {path.relative_to(ROOT)}:{node.lineno}")
    for directory in (ROOT / "schemas", ROOT / "examples"):
        for path in directory.glob("*.json"):
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors += fail(f"Invalid JSON {path.relative_to(ROOT)}: {exc}")
    collector_text = (ROOT / "cast_offline_collector.py").read_text(encoding="utf-8")
    version_match = re.search(r'^COLLECTOR_VERSION = "([^"]+)"', collector_text, re.MULTILINE)
    project_match = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE)
    if not version_match or not project_match or version_match.group(1) != project_match.group(1):
        errors += fail("Version mismatch between collector and pyproject.toml")
    if errors:
        return 1
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, check=False)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
