#!/usr/bin/env python3
"""Audit a local root directory received for the modified-makefiles workflow.

The tool is intentionally diagnostic. It does not try to turn a raw CMake
build directory into a CAST bundle because compiler probes, Conan exports and
source trees must come from the client environment that performed the build.
"""

import argparse
import json
import sys
from pathlib import Path


REQUIRED_INPUTS = (
    ("compile database", ("compilation/compile_commands.json", "compile_commands.json", "compilation/compilation-units.json")),
    ("Conan packages inventory", ("conan/packages.json", "packages.json")),
    ("Conan graph", ("conan/conan-graph.json", "conan-graph.json")),
    ("compiler variants", ("compiler/qcc-variants.json", "qcc-variants.json")),
    ("compiler macros probes", (".macros.txt",)),
    ("compiler include probes", (".includes.txt",)),
)
SOURCE_EXTENSIONS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx")


class Snapshot:
    def __init__(self, origin, names, reader):
        self.origin = origin
        self.names = sorted({normalize(name) for name in names if normalize(name)})
        self._reader = reader

    def has_exact_or_suffix(self, candidates):
        for name in self.names:
            for candidate in candidates:
                candidate = normalize(candidate)
                if candidate.endswith("/"):
                    if name.startswith(candidate) or f"/{candidate}" in name:
                        return True
                    continue
                if name == candidate or name.endswith("/" + candidate) or name.endswith(candidate):
                    return True
        return False

    def find_suffix(self, suffix):
        suffix = normalize(suffix)
        return [name for name in self.names if name.endswith(suffix)]

    def read_text_by_suffix(self, suffix, limit=200000):
        matches = self.find_suffix(suffix)
        if not matches:
            return ""
        data = self._reader(matches[0])
        return data[:limit]


def normalize(name):
    return str(name).replace("\\", "/").strip("/")


def snapshot_from_root(path):
    path = path.resolve()
    if path.is_dir():
        names = [item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file()]

        def reader(relative):
            return (path / relative).read_text(encoding="utf-8", errors="replace")

        return Snapshot(str(path), names, reader)
    raise SystemExit(f"Root directory does not exist: {path}")


def cmake_compile_commands_state(snapshot):
    cache = snapshot.read_text_by_suffix("CMakeCache.txt")
    if "CMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON" in cache:
        return "enabled"
    if "CMAKE_EXPORT_COMPILE_COMMANDS:BOOL=" in cache:
        return "disabled_or_empty"
    if snapshot.find_suffix("build.make"):
        return "unknown"
    return "absent"


def has_source_tree(snapshot):
    for name in snapshot.names:
        if "/CMakeFiles/" in name or name.startswith("build/"):
            continue
        if not name.lower().endswith(SOURCE_EXTENSIONS):
            continue
        parts = name.split("/")
        if parts[0] in {"source", "src", "include"} or {"source", "src", "include"}.intersection(parts):
            return True
    return False


def audit(snapshot):
    missing = []
    if not has_source_tree(snapshot):
        missing.append("source tree")
    for label, candidates in REQUIRED_INPUTS:
        if not snapshot.has_exact_or_suffix(candidates):
            missing.append(label)

    evidence = []
    if snapshot.find_suffix("CMakeCache.txt"):
        evidence.append("CMakeCache.txt present")
    if snapshot.find_suffix("build.make") and snapshot.find_suffix("flags.make"):
        evidence.append("CMake build.make and flags.make present")
    if snapshot.find_suffix(".o.d"):
        evidence.append("compiler dependency files present")
    if snapshot.find_suffix("conanbuildinfo.txt") or snapshot.find_suffix("conaninfo.txt"):
        evidence.append("Conan 1 build metadata present")
    if snapshot.find_suffix("graph_info.json"):
        evidence.append("graph_info.json present")

    warnings = []
    cmake_state = cmake_compile_commands_state(snapshot)
    if cmake_state == "disabled_or_empty":
        warnings.append("CMAKE_EXPORT_COMPILE_COMMANDS is empty or disabled in CMakeCache.txt")
    if "compile database" in missing and "CMake build.make and flags.make present" in evidence:
        warnings.append("CMake build files may help reconstruct commands for diagnosis, but they do not replace a client-produced compile_commands.json")
    if "Conan packages inventory" in missing and "Conan 1 build metadata present" in evidence:
        warnings.append("conanbuildinfo.txt/conaninfo.txt identify dependencies partially, but they do not replace packages.json and exported host headers")
    if "compiler macros probes" in missing or "compiler include probes" in missing:
        warnings.append("compiler probes must be generated with the client toolchain, SDK, sysroot and compiler variant")

    status = "READY_FOR_LOCAL_EXPORT" if not missing else "INCOMPLETE"
    if missing and evidence:
        status = "DIAGNOSTIC_ONLY"

    return {
        "origin": snapshot.origin,
        "status": status,
        "missing": missing,
        "evidence": evidence,
        "warnings": warnings,
        "required_action": required_action(missing),
    }


def required_action(missing):
    if not missing:
        return "Run makefile_local_export.py on the staged root, then send CAST_DELIVERABLES_BUNDLE."
    return "Ask the client to regenerate a local CAST staging directory from the modified makefiles and include every missing input."


def format_text(report):
    lines = [f"Origin: {report['origin']}", f"Status: {report['status']}"]
    if report["missing"]:
        lines.append("")
        lines.append("Missing strict inputs:")
        lines.extend(f"- {item}" for item in report["missing"])
    if report["evidence"]:
        lines.append("")
        lines.append("Evidence found:")
        lines.extend(f"- {item}" for item in report["evidence"])
    if report["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in report["warnings"])
    lines.append("")
    lines.append("Required action:")
    lines.append(f"- {report['required_action']}")
    return "\n".join(lines)


def parser():
    result = argparse.ArgumentParser(description="Audit a received local root directory for the modified-makefiles CAST workflow.")
    result.add_argument("--root", required=True, type=Path, help="Root directory received from the client")
    result.add_argument("--format", choices=("text", "json"), default="text")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    report = audit(snapshot_from_root(args.root))
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_text(report))
    return 0 if report["status"] == "READY_FOR_LOCAL_EXPORT" else 2


if __name__ == "__main__":
    sys.exit(main())
