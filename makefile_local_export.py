#!/usr/bin/env python3
"""Local exporter for projects whose modified makefiles already stage CAST inputs.

This variant does not replace client_ci_export.py. It is meant for repositories
where the client supplied modified makefiles that copy CAST deliverables during
a local qualified build. The script validates and normalizes that staged
directory into CAST_DELIVERABLES_BUNDLE without invoking Conan, Make, or any
compiler.
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_DIRECTORIES = ("source", "compilation", "conan", "compiler")
OPTIONAL_DIRECTORIES = ("identity", "generated", "build", "logs", "qnx", "cast-analysis-logs")
REQUIRED_FILES = ("conan/packages.json", "conan/conan-graph.json")


def is_relative_to(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def copy_tree_safe(source, destination):
    if not source.is_dir():
        return 0
    source_root = source.resolve()
    count = 0

    def visit(physical, relative, ancestors):
        nonlocal count
        resolved_directory = physical.resolve(strict=True)
        if not is_relative_to(resolved_directory, source_root):
            raise RuntimeError(f"Directory link escapes staged root: {physical}")
        if resolved_directory in ancestors:
            raise RuntimeError(f"Directory link cycle detected: {physical}")
        for entry in sorted(os.scandir(resolved_directory), key=lambda item: item.name):
            path = Path(entry.path)
            logical = relative / entry.name
            if entry.is_symlink():
                resolved = path.resolve(strict=True)
                if not is_relative_to(resolved, source_root):
                    raise RuntimeError(f"Symbolic link escapes staged root: {path}")
                if resolved.is_dir():
                    visit(resolved, logical, ancestors | {resolved_directory})
                    continue
                candidate = resolved
            elif entry.is_dir(follow_symlinks=False):
                visit(path, logical, ancestors | {resolved_directory})
                continue
            elif entry.is_file(follow_symlinks=False):
                candidate = path
            else:
                continue
            target = destination / logical
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            count += 1

    visit(source_root, Path(), set())
    return count


def local_run_id(explicit):
    if explicit:
        return explicit
    return "local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_identity(bundle, args):
    identity_path = bundle / "identity" / "BUILD_IDENTITY.json"
    if identity_path.is_file() and not args.force_identity:
        return "provided"

    run_id = local_run_id(args.run_id)
    required = {
        "application": args.application,
        "git commit": args.git_commit,
        "target label": args.target_label,
        "target OS": args.target_os,
        "architecture": args.architecture,
        "build type": args.build_type,
        "compiler": args.compiler,
        "compiler variant": args.compiler_variant,
        "host profile": args.host_profile,
        "build profile": args.build_profile,
        "source root at build": args.source_root_at_build,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit("Missing identity values: " + ", ".join(missing))

    identity = {
        "schema_version": 1,
        "application": args.application,
        "application_version": args.application_version,
        "git_commit": args.git_commit,
        "pipeline_id": run_id,
        "job_id": "local",
        "build_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "target": {
            "label": args.target_label,
            "os": args.target_os,
            "os_version": args.target_os_version,
            "architecture": args.architecture,
            "build_type": args.build_type,
        },
        "compiler": {
            "driver": args.compiler,
            "version": args.compiler_version,
            "variant": args.compiler_variant,
        },
        "conan": {
            "version": args.conan_version,
            "host_profile": args.host_profile,
            "build_profile": args.build_profile,
        },
        "paths": {
            "source_root": args.source_root_at_build,
            "generated_root": args.generated_root_at_build,
            "qnx_target": args.qnx_target_at_build,
        },
        "generated_code": {
            "version": args.generator_version,
            "generation_id": args.generation_id,
            "source_model_commit": args.generated_source_commit or args.git_commit,
        },
    }
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return "generated"


def validate_bundle(bundle):
    issues = []
    for directory in REQUIRED_DIRECTORIES:
        if not (bundle / directory).is_dir():
            issues.append(f"missing directory: {directory}/")
    has_compile_commands = (bundle / "compilation" / "compile_commands.json").is_file()
    has_compilation_units = (bundle / "compilation" / "compilation-units.json").is_file()
    if not has_compile_commands and not has_compilation_units:
        issues.append("missing file: compilation/compile_commands.json or compilation/compilation-units.json")
    for relative in REQUIRED_FILES:
        if not (bundle / relative).is_file():
            issues.append(f"missing file: {relative}")
    if not (bundle / "identity" / "BUILD_IDENTITY.json").is_file():
        issues.append("missing file: identity/BUILD_IDENTITY.json")
    return issues


def parser():
    result = argparse.ArgumentParser(
        description="Normalize a local makefile-staged CAST drop into CAST_DELIVERABLES_BUNDLE."
    )
    result.add_argument("--staged-root", required=True, type=Path, help="Local directory populated by modified makefiles")
    result.add_argument("--bundle", required=True, type=Path, help="Output CAST_DELIVERABLES_BUNDLE")
    result.add_argument("--force-identity", action="store_true", help="Regenerate identity even if the staged root provides one")
    result.add_argument("--application", default="")
    result.add_argument("--application-version", default="")
    result.add_argument("--git-commit", default="")
    result.add_argument("--run-id", help="Local run identifier; generated when omitted")
    result.add_argument("--target-label", default="")
    result.add_argument("--target-os", default="")
    result.add_argument("--target-os-version", default="")
    result.add_argument("--architecture", default="")
    result.add_argument("--build-type", default="")
    result.add_argument("--compiler", default="")
    result.add_argument("--compiler-version", default="")
    result.add_argument("--compiler-variant", default="")
    result.add_argument("--conan-version", default="")
    result.add_argument("--host-profile", default="")
    result.add_argument("--build-profile", default="")
    result.add_argument("--source-root-at-build", default="")
    result.add_argument("--generated-root-at-build", default="")
    result.add_argument("--qnx-target-at-build", default="")
    result.add_argument("--generator-version", default="")
    result.add_argument("--generation-id", default="")
    result.add_argument("--generated-source-commit", default="")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    staged_root = args.staged_root.resolve()
    bundle = args.bundle.resolve()
    if not staged_root.is_dir():
        raise SystemExit(f"Staged root does not exist: {staged_root}")
    if is_relative_to(bundle, staged_root):
        raise SystemExit("Bundle output must not be located inside the staged root")
    if bundle.exists() and any(bundle.iterdir()):
        raise SystemExit(f"Bundle must be absent or empty: {bundle}")
    bundle.mkdir(parents=True, exist_ok=True)

    copied = {}
    for directory in REQUIRED_DIRECTORIES + OPTIONAL_DIRECTORIES:
        source = staged_root / directory
        if source.exists():
            copied[directory] = copy_tree_safe(source, bundle / directory)

    identity_status = write_identity(bundle, args)
    issues = validate_bundle(bundle)
    status = "READY_FOR_TRANSFER" if not issues else "NOT_READY_FOR_TRANSFER"
    result = {
        "bundle": str(bundle),
        "staged_root": str(staged_root),
        "status": status,
        "identity": identity_status,
        "copied": copied,
        "issues": issues,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not issues else 2


if __name__ == "__main__":
    sys.exit(main())
