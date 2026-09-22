#!/usr/bin/env python3
"""Recover partial CAST inputs from a materialized local root directory.

This fallback is for cases where a complete modified-makefiles staging cannot
be regenerated, but a delivered source/build tree is available locally. The
script scans the root directory directly on Linux or Windows.
"""

import argparse
import json
import re
import shlex
import shutil
import sys
from pathlib import Path


COMPILE_COMMAND_PATTERN = re.compile(r"^\s*(?P<compiler>\S+myCMakeQCC\.bat|\S*qcc\S*|\S*gcc\S*)\s+(?P<args>.*\s-c\s+.*)$", re.IGNORECASE)
VARIABLE_PATTERN = re.compile(r"\$\(([^)]+)\)")
CONAN_CACHE_PATTERN = re.compile(
    r"(?P<root>.*?\.conan[/\\]data[/\\](?P<name>[^/\\]+)[/\\](?P<version>[^/\\]+)[/\\][^/\\]+[/\\][^/\\]+[/\\]package[/\\](?P<package_id>[^/\\\s]+))",
    re.IGNORECASE,
)
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
TRACE_SUFFIXES = {".d", ".rsp", ".response"}


def is_relative_to(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def relative_posix(path, root):
    return path.relative_to(root).as_posix()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def discover_applications(root):
    applications = []
    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "build").is_dir():
            applications.append(child)
    if (root / "build").is_dir():
        applications.append(root)
    return applications


def selected_applications(root, application):
    applications = discover_applications(root)
    if not application:
        return applications
    selected = [item for item in applications if item.name == application]
    if not selected:
        raise SystemExit(f"Application not found under root: {application}")
    return selected


def application_summary(app_root):
    build_root = app_root / "build"
    build_files = [item for item in build_root.rglob("*") if item.is_file()] if build_root.is_dir() else []
    source_files = [
        item for folder in (app_root / "src", app_root / "include")
        if folder.is_dir()
        for item in folder.rglob("*")
        if item.is_file() and item.suffix.lower() in SOURCE_SUFFIXES
    ]
    return {
        "build_roots": [str(build_root)] if build_root.is_dir() else [],
        "build_files": len(build_files),
        "source_files": len(source_files),
        "build_make_files": len([item for item in build_files if item.name == "build.make"]),
        "flags_make_files": len([item for item in build_files if item.name == "flags.make"]),
        "dependency_files": len([item for item in build_files if item.name.endswith(".o.d")]),
    }


def conan_header_files(root):
    conan_root = root / ".conan" / "data"
    if not conan_root.is_dir():
        return []
    return [
        item for item in conan_root.rglob("*")
        if item.is_file() and "include" in item.parts and item.suffix.lower() in SOURCE_SUFFIXES
    ]


def compiler_probe_files(app_roots):
    result = []
    for app_root in app_roots:
        for item in app_root.rglob("*"):
            if item.is_file() and (item.name.endswith(".macros.txt") or item.name.endswith(".includes.txt") or item.name == "qcc-variants.json"):
                result.append(item)
    return result


def parse_make_variables(path):
    variables = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in raw or raw.lstrip().startswith("#"):
            continue
        name, value = raw.split("=", 1)
        variables[name.strip()] = value.strip()
    return variables


def expand_variables(value, variables):
    def replace(match):
        return variables.get(match.group(1), match.group(0))

    previous = None
    current = value
    while previous != current:
        previous = current
        current = VARIABLE_PATTERN.sub(replace, current)
    return current


def parse_command(command):
    return shlex.split(command.replace("\\", "\\\\"), posix=False)


def recover_compile_commands(build_root):
    entries = []
    for build_make in sorted(build_root.rglob("build.make")):
        flags_make = build_make.parent / "flags.make"
        if not flags_make.is_file():
            continue
        variables = parse_make_variables(flags_make)
        build_dir = ""
        for raw in build_make.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw.startswith("CMAKE_BINARY_DIR ="):
                build_dir = raw.split("=", 1)[1].strip()
            match = COMPILE_COMMAND_PATTERN.match(raw)
            if not match:
                continue
            expanded = expand_variables(f"{match.group('compiler')} {match.group('args')}", variables)
            arguments = parse_command(expanded)
            if "-c" not in arguments:
                continue
            source = arguments[arguments.index("-c") + 1]
            entries.append({
                "directory": build_dir or str(build_make.parent),
                "file": source,
                "arguments": arguments,
            })
    return entries


def first_existing(root, names):
    for name in names:
        matches = sorted(root.rglob(name))
        if matches:
            return matches[0]
    return None


def parse_conaninfo(path):
    full_requires = {}
    section = ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]")
            continue
        if section == "full_requires" and ":" in line:
            reference, package_id = line.rsplit(":", 1)
            full_requires[reference.strip()] = package_id.strip()
    return full_requires


def parse_rootpaths(path):
    rootpaths = {}
    current = ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("[rootpath_") and line.endswith("]"):
            current = line[len("[rootpath_"):-1]
            continue
        if current and line:
            rootpaths[current] = line
            current = ""
    return rootpaths


def package_from_cache_path(root):
    match = CONAN_CACHE_PATTERN.search(root)
    if not match:
        return None
    return {
        "name": match.group("name"),
        "version": match.group("version"),
        "package_id": match.group("package_id"),
        "root": match.group("root").replace("\\", "/"),
    }


def recover_conan(build_root):
    conaninfo = first_existing(build_root, ["conaninfo.txt"])
    conanbuildinfo = first_existing(build_root, ["conanbuildinfo.txt"])
    requires = parse_conaninfo(conaninfo) if conaninfo else {}
    rootpaths = parse_rootpaths(conanbuildinfo) if conanbuildinfo else {}
    packages = []
    seen = set()

    for reference, package_id in requires.items():
        name = reference.split("/", 1)[0]
        rootpath = rootpaths.get(name, "")
        parsed = package_from_cache_path(rootpath) if rootpath else None
        key = (reference, package_id)
        seen.add(key)
        packages.append({
            "reference": reference,
            "recipe_revision": "",
            "context": "host",
            "package_id": package_id,
            "package_revision": "",
            "original_root": rootpath,
            "logical_root": f"conan://{reference}/{package_id}",
            "exported_root": "",
            "recovery": "headers_not_available",
            "cache_name": parsed["name"] if parsed else name,
        })

    for name, rootpath in rootpaths.items():
        parsed = package_from_cache_path(rootpath)
        if not parsed:
            continue
        reference = f"{parsed['name']}/{parsed['version']}"
        key = (reference, parsed["package_id"])
        if key in seen:
            continue
        packages.append({
            "reference": reference,
            "recipe_revision": "",
            "context": "host",
            "package_id": parsed["package_id"],
            "package_revision": "",
            "original_root": rootpath,
            "logical_root": f"conan://{reference}/{parsed['package_id']}",
            "exported_root": "",
            "recovery": "headers_not_available",
            "cache_name": name,
        })
    return packages


def copy_recovery_inputs(root, app_roots, output):
    counts = {
        "copied_source_files": 0,
        "copied_conan_header_files": 0,
        "copied_build_traces": 0,
        "copied_probe_files": 0,
    }
    for app_root in app_roots:
        for folder_name in ("src", "include"):
            folder = app_root / folder_name
            if not folder.is_dir():
                continue
            for item in folder.rglob("*"):
                if item.is_file() and item.suffix.lower() in SOURCE_SUFFIXES:
                    copy_file(item, output / "source" / relative_posix(item, root))
                    counts["copied_source_files"] += 1
        build_root = app_root / "build"
        if build_root.is_dir():
            for item in build_root.rglob("*"):
                if item.is_file() and item.suffix.lower() in TRACE_SUFFIXES:
                    copy_file(item, output / "build" / relative_posix(item, root))
                    counts["copied_build_traces"] += 1

    for item in conan_header_files(root):
        copy_file(item, output / "conan" / "export-recovered" / relative_posix(item, root))
        counts["copied_conan_header_files"] += 1

    for item in compiler_probe_files(app_roots):
        copy_file(item, output / "compiler" / relative_posix(item, root))
        counts["copied_probe_files"] += 1

    return counts


def recover(args):
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"Root directory does not exist: {root}")

    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Output directory must be absent or empty: {output}")
    if is_relative_to(output, root):
        raise SystemExit("Output directory must not be located inside the scanned root")
    output.mkdir(parents=True, exist_ok=True)

    app_roots = selected_applications(root, args.application)
    summaries = {app.name: application_summary(app) for app in app_roots}
    build_roots = [app / "build" for app in app_roots if (app / "build").is_dir()]
    conan_headers = conan_header_files(root)
    probes = compiler_probe_files(app_roots)

    compile_commands = []
    packages = []
    for build_root in build_roots:
        compile_commands.extend(recover_compile_commands(build_root))
        packages.extend(recover_conan(build_root))

    copied = copy_recovery_inputs(root, app_roots, output)
    if compile_commands:
        write_json(output / "compilation" / "compile_commands.json", compile_commands)
    if packages:
        write_json(output / "conan" / "packages.recovered.json", {"schema_version": 1, "packages": packages})
        write_json(output / "conan" / "conan-graph.recovered.json", {
            "recovery": "partial_conan1_metadata",
            "packages": [{"ref": item["reference"], "context": item["context"], "package_id": item["package_id"]} for item in packages],
        })

    write_json(output / "root-build-files.json", {
        "root": str(root),
        "application_filter": args.application,
        "applications_detected": [app.name for app in app_roots],
        "applications": summaries,
        "build_roots": [str(path) for path in build_roots],
        "conan_header_files": [relative_posix(path, root) for path in conan_headers],
        "compiler_probe_files": [relative_posix(path, root) for path in probes],
    })

    missing = []
    if not compile_commands:
        missing.append("compile commands")
    if not packages:
        missing.append("Conan package metadata")
    if not copied["copied_source_files"]:
        missing.append("source/header files")
    if conan_headers and not copied["copied_conan_header_files"]:
        missing.append("Conan exported headers")
    if not probes:
        missing.append("QCC macro/include probes")

    report = {
        "status": "RECOVERED_PARTIAL" if missing else "RECOVERED_WITH_LOCAL_SUPPLEMENTS",
        "root": str(root),
        "output": str(output),
        "application_filter": args.application,
        "applications_detected": [app.name for app in app_roots],
        "applications_count": len(app_roots),
        "applications": summaries,
        "build_roots": len(build_roots),
        "conan_header_files": len(conan_headers),
        "compiler_probe_files": len(probes),
        "compile_commands": len(compile_commands),
        "conan_packages": len(packages),
        **copied,
        "missing_for_strict_cast": missing,
        "notes": [
            "Recovery scans a materialized root directory directly; it does not read text listings or archives.",
            "Use --application only when a single module must be isolated; omit it to process every detected application.",
            "Recovered compile_commands.json is reconstructed from CMake build.make and flags.make when those files are present.",
            "Recovered Conan metadata is inferred from Conan 1 text files and may still require review before strict CAST use.",
        ],
    }
    write_json(output / "recovery-report.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not missing else 2


def parser():
    result = argparse.ArgumentParser(description="Recover partial CAST inputs from a materialized local root directory.")
    result.add_argument("--root", required=True, type=Path, help="Root directory containing applications, build directories and optional .conan cache")
    result.add_argument("--output", required=True, type=Path)
    result.add_argument("--application", default="", help="Optional application/module filter; omitted means all detected applications")
    return result


def main(argv=None):
    return recover(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
