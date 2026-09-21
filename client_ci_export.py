#!/usr/bin/env python3
"""Client-side post-build exporter. It never launches Conan or a compiler."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


HEADER_EXTENSIONS = {".h", ".hh", ".hpp", ".hxx", ".inc", ".inl", ".ipp", ".tcc", ".tpp", ".def", ".cfg", ".config"}
SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx", ".inc", ".inl", ".ipp", ".tcc", ".tpp", ".def", ".cfg", ".config", ".py"}
def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "item"


def is_relative_to(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_probably_text(path):
    try:
        sample = path.read_bytes()[:8192]
    except OSError:
        return False
    return b"\x00" not in sample


def selected_file(path, extensions):
    if extensions is None:
        return True
    return path.suffix.lower() in extensions or (not path.suffix and is_probably_text(path))


def copy_selected(source, destination, extensions=None):
    if not source or not source.is_dir():
        return 0
    root = source.resolve()
    count = 0

    def visit(physical, relative, ancestors):
        nonlocal count
        resolved_directory = physical.resolve(strict=True)
        if not is_relative_to(resolved_directory, root):
            raise RuntimeError(f"Directory link escapes source root: {physical}")
        if resolved_directory in ancestors:
            raise RuntimeError(f"Directory link cycle detected: {physical}")
        for entry in sorted(os.scandir(resolved_directory), key=lambda item: item.name):
            path = Path(entry.path)
            logical = relative / entry.name
            if entry.is_symlink():
                resolved = path.resolve(strict=True)
                if not is_relative_to(resolved, root):
                    raise RuntimeError(f"Symbolic link escapes source root: {path}")
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
            if not selected_file(candidate, extensions):
                continue
            target = destination / logical
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, target)
            count += 1

    visit(root, Path(), set())
    return count


def copy_file(source, destination):
    if not source:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_log(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def environment_value(explicit, *names):
    if explicit:
        return explicit
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    return ""


def parser():
    result = argparse.ArgumentParser(description="Prepare CAST_EVIDENCE_BUNDLE after a successful client build.")
    result.add_argument("--bundle", required=True, type=Path)
    result.add_argument("--source", required=True, type=Path)
    result.add_argument("--generated", type=Path)
    result.add_argument("--build-root", required=True, type=Path)
    result.add_argument("--compile-commands", required=True, type=Path)
    result.add_argument("--build-log", action="append", default=[], type=Path)
    result.add_argument("--conan-packages", required=True, type=Path, help="Exact package manifest made inside the Conan environment")
    result.add_argument("--conan-graph", required=True, type=Path)
    result.add_argument("--conan-lockfile", action="append", default=[], type=Path)
    result.add_argument("--conan-profile", action="append", default=[], type=Path)
    result.add_argument("--qnx-target", type=Path)
    result.add_argument("--qcc-probe-dir", type=Path)
    result.add_argument("--application", required=True)
    result.add_argument("--application-version", default="")
    result.add_argument("--git-commit")
    result.add_argument("--pipeline-id")
    result.add_argument("--job-id")
    result.add_argument("--target-label", required=True)
    result.add_argument("--target-os", required=True)
    result.add_argument("--target-os-version", default="")
    result.add_argument("--architecture", required=True)
    result.add_argument("--build-type", required=True)
    result.add_argument("--compiler", required=True)
    result.add_argument("--compiler-version", default="")
    result.add_argument("--compiler-variant", required=True)
    result.add_argument("--conan-version", default="")
    result.add_argument("--host-profile", required=True)
    result.add_argument("--build-profile", required=True)
    result.add_argument("--source-root-at-build", required=True)
    result.add_argument("--generated-root-at-build", default="")
    result.add_argument("--qnx-target-at-build", default="")
    result.add_argument("--matlab-version", default="")
    result.add_argument("--generation-id", default="")
    result.add_argument("--generated-source-commit", default="")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    bundle = args.bundle.resolve()
    if bundle.exists() and any(bundle.iterdir()):
        raise SystemExit(f"Bundle must be absent or empty: {bundle}")
    bundle.mkdir(parents=True, exist_ok=True)

    git_commit = environment_value(args.git_commit, "CI_COMMIT_SHA", "GITHUB_SHA", "BUILD_SOURCEVERSION")
    pipeline_id = environment_value(args.pipeline_id, "CI_PIPELINE_ID", "GITHUB_RUN_ID", "BUILD_BUILDID")
    job_id = environment_value(args.job_id, "CI_JOB_ID", "GITHUB_JOB", "SYSTEM_JOBID")
    if not git_commit or not pipeline_id:
        raise SystemExit("Git commit and pipeline ID must be provided explicitly or by a supported CI variable")
    identity = {
        "schema_version": 1,
        "application": args.application, "application_version": args.application_version,
        "git_commit": git_commit, "pipeline_id": pipeline_id, "job_id": job_id,
        "build_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "target": {"label": args.target_label, "os": args.target_os, "os_version": args.target_os_version, "architecture": args.architecture, "build_type": args.build_type},
        "compiler": {"driver": args.compiler, "version": args.compiler_version, "variant": args.compiler_variant},
        "conan": {"version": args.conan_version, "host_profile": args.host_profile, "build_profile": args.build_profile},
        "paths": {"source_root": args.source_root_at_build, "generated_root": args.generated_root_at_build, "qnx_target": args.qnx_target_at_build},
        "generated_code": {"version": args.matlab_version, "generation_id": args.generation_id, "source_model_commit": args.generated_source_commit or git_commit},
    }
    identity_path = bundle / "identity" / "BUILD_IDENTITY.json"
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    copy_selected(args.source.resolve(), bundle / "source", SOURCE_EXTENSIONS)
    if args.generated:
        copy_selected(args.generated.resolve(), bundle / "generated", SOURCE_EXTENSIONS)
    copy_file(args.compile_commands.resolve(), bundle / "compilation" / "compile_commands.json")
    copy_selected(args.build_root.resolve(), bundle / "build", {".d", ".rsp", ".response"})
    for index, log in enumerate(args.build_log, 1):
        copy_log(log.resolve(), bundle / "logs" / f"{index:03d}_{log.name}")
    copy_file(args.conan_graph.resolve(), bundle / "conan" / "conan-graph.json")
    for path in args.conan_lockfile:
        copy_file(path.resolve(), bundle / "conan" / "lockfiles" / path.name)
    for path in args.conan_profile:
        copy_file(path.resolve(), bundle / "conan" / "profiles" / path.name)

    package_data = json.loads(args.conan_packages.read_text(encoding="utf-8"))
    packages = package_data.get("packages", []) if isinstance(package_data, dict) else package_data
    for index, package in enumerate(packages):
        if package.get("context") != "host":
            continue
        original = Path(str(package.get("original_root") or ""))
        if not original.is_dir():
            raise RuntimeError(f"Conan host package root unavailable: {original}")
        name = safe_name(str(package.get("reference") or f"package-{index}"))
        exported = bundle / "conan" / "export" / f"{index:04d}-{name}"
        copied = copy_selected(original, exported, HEADER_EXTENSIONS)
        if not copied:
            raise RuntimeError(f"No headers exported for Conan host package: {package.get('reference')}")
        package["exported_root"] = exported.relative_to(bundle).as_posix()
        package.setdefault("logical_root", "")
    output_packages = {"schema_version": 1, "packages": packages}
    packages_path = bundle / "conan" / "packages.json"
    packages_path.parent.mkdir(parents=True, exist_ok=True)
    packages_path.write_text(json.dumps(output_packages, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.qnx_target:
        copy_selected(args.qnx_target.resolve(), bundle / "qnx", HEADER_EXTENSIONS)
    if args.qcc_probe_dir:
        copy_selected(args.qcc_probe_dir.resolve(), bundle / "compiler", None)

    rows = []
    for path in sorted(item for item in bundle.rglob("*") if item.is_file() and item.name != "FILES.sha256"):
        rows.append(f"{sha256(path)}  {path.relative_to(bundle).as_posix()}")
    (bundle / "FILES.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(json.dumps({"bundle": str(bundle), "files": len(rows), "status": "READY_FOR_TRANSFER"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
