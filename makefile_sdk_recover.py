#!/usr/bin/env python3
"""Prepare an exploratory CAST package from a reviewed Makefile/SDK profile.

No Makefile or shell code is evaluated. Python 3.9+ standard library only.
"""

import argparse
import json
import re
import shutil
import sys
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath, PureWindowsPath

import cast_offline_collector as collector


PROVENANCE = "reconstructed_from_reviewed_makefile_sdk_profile"
BASE_RESERVATIONS = [
    "Compilation commands are reconstructed, not observed during a build.",
    "The profile must match the selected target and any build-time overrides.",
    "Compiler predefined macros and implicit include search order are unverified.",
    "SDK environment-setup.d scripts and their build-time effects are unverified.",
    "Compiler version and delivered binary/source correspondence are unverified.",
]


def progress(message):
    print("[makefile-sdk] " + message, file=sys.stderr, flush=True)


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Profile paths must use nonempty relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts:
        raise ValueError("Profile path escapes its base: " + value)
    return value


def input_path(root, value):
    candidate = root / relative_path(value)
    if not collector.is_relative_to(candidate.resolve(), root):
        raise ValueError("Input path escapes --root: " + value)
    return candidate


def load_profile(path):
    profile = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise ValueError("Expected a profile object with schema_version=1")
    for key in ("application_roots", "source_globs", "evidence_files"):
        values = profile.get(key)
        if not isinstance(values, list) or not values:
            raise ValueError("Expected a nonempty list: " + key)
        for value in values:
            relative_path(value)
            if key != "source_globs" and any(char in value for char in "*?["):
                raise ValueError("Wildcards are only allowed in source_globs")
    for key in ("compiler", "architecture"):
        if not isinstance(profile.get(key), str) or not profile[key]:
            raise ValueError("Expected a nonempty string: " + key)
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", profile["compiler"]):
        raise ValueError("compiler must be a driver name, without paths or arguments")
    flags = profile.get("flags")
    if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
        raise ValueError("flags must be an argument list")
    # Paths, source selection and macro actions have dedicated structured fields.
    for flag in flags:
        if not flag.startswith("-") or re.match(r"^-(?:I|D|U|include|imacros|isystem|iquote|idirafter|isysroot|o|c$)", flag):
            raise ValueError("Use structured fields for source/path/macro options: " + flag)
        if ("sysroot" in flag or "/" in flag or "\\" in flag or "$" in flag
                or flag.startswith(("-Wp,", "-Xpreprocessor", "--include", "--define", "--undefine"))):
            raise ValueError("Unresolved or path-bearing compiler flag: " + flag)
    defines = profile.get("defines")
    if not isinstance(defines, list) or not all(
        isinstance(value, str) and re.fullmatch(r"[A-Za-z_]\w*(?:=[^\r\n]*)?", value)
        for value in defines
    ):
        raise ValueError("defines must contain object-like macro definitions")
    names = [collector.macro_name(value) for value in defines]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate macro names in profile")
    includes = profile.get("includes")
    if not isinstance(includes, list) or not includes:
        raise ValueError("includes must be a nonempty ordered list")
    dependency_names = set()
    for item in includes:
        if not isinstance(item, dict):
            raise ValueError("Each include must be an object")
        relative_path(item.get("path"))
        if item.get("kind") not in ("I", "isystem"):
            raise ValueError("Include kind must be I or isystem")
        name = item.get("dependency")
        if name is not None:
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise ValueError("Invalid dependency name")
            if name in dependency_names:
                raise ValueError("Use one copied root per dependency; use subdirectories for includes")
            dependency_names.add(name)
        subdirs = item.get("subdirectories", ["."])
        if not isinstance(subdirs, list) or not subdirs:
            raise ValueError("subdirectories must be a nonempty ordered list")
        for subdir in subdirs:
            relative_path(subdir)
    sysroot = profile.get("sysroot")
    if not isinstance(sysroot, dict):
        raise ValueError("sysroot must be an object")
    relative_path(sysroot.get("path"))
    subdirs = sysroot.get("include_subdirectories")
    if not isinstance(subdirs, list) or not subdirs:
        raise ValueError("sysroot.include_subdirectories must be a nonempty list")
    for subdir in subdirs:
        relative_path(subdir)
        if subdir == ".":
            raise ValueError("Select sysroot include directories, not the entire SDK")
    if "sdk" in dependency_names:
        raise ValueError("The dependency name sdk is reserved for the target sysroot")
    return profile


def recover(args):
    progress("Starting local recovery; no Makefile, shell script or compiler will be executed.")
    root = args.root.resolve()
    output = args.output.resolve()
    archive = output.with_name(output.name + ".zip")
    if not root.is_dir():
        raise ValueError("--root must be a materialized directory")
    if collector.is_relative_to(output, root) or collector.is_relative_to(root, output):
        raise ValueError("--root and --output must be disjoint directories")
    if output.exists() or archive.exists():
        raise ValueError("--output and its ZIP must not already exist")
    profile = load_profile(args.profile)
    progress("Profile validated. Scanning input directories...")
    issues = []
    copied = []
    copied_paths = set()
    failed = {}
    source_files = []
    include_paths = []
    command_include_flags = []

    def issue(code, path):
        issues.append({"code": code, "path": path})

    # Build a source/destination plan before writing. Conflicting destinations
    # indicate overlapping or inconsistent roots in the reviewed profile.
    planned = {}

    def plan_file(path, destination, category):
        relative = path.relative_to(root).as_posix()
        if not collector.is_relative_to(path.resolve(), root):
            failed[relative] = "symlink_target_outside_root"
            issue("COPY-OUTSIDE-ROOT", relative)
            return
        key = destination.casefold()
        if key in planned and planned[key][0] != path:
            raise ValueError("Case-insensitive output collision: " + destination)
        planned[key] = (path, destination, category)

    def plan_tree(value, destination, category):
        progress("Scanning " + value)
        path = input_path(root, value)
        if not path.is_dir():
            issue("MISSING-INCLUDE-TREE", value)
            return
        for index, item in enumerate(path.rglob("*"), 1):
            if index % 1000 == 0:
                progress("Scanning " + value + ": " + str(index) + " entries visited")
            if item.is_symlink() and not collector.is_relative_to(item.resolve(), root):
                relative = item.relative_to(root).as_posix()
                failed[relative] = "symlink_target_outside_root"
                issue("COPY-OUTSIDE-ROOT", relative)
                continue
            if item.is_symlink() and item.is_dir():
                failed[item.relative_to(root).as_posix()] = "symlink_directory_not_traversed"
                issue("SYMLINK-DIRECTORY", item.relative_to(root).as_posix())
                continue
            if not item.is_file():
                continue
            suffix = item.suffix.lower()
            allowed = collector.HEADER_EXTENSIONS | (collector.SOURCE_EXTENSIONS if category == "application" else set())
            if suffix not in allowed and suffix != "":
                continue
            if not suffix:
                try:
                    with item.open("rb") as stream:
                        if b"\x00" in stream.read(8192):
                            continue
                except OSError:
                    failed[item.relative_to(root).as_posix()] = "unreadable_file"
                    issue("COPY-UNREADABLE", item.relative_to(root).as_posix())
                    continue
            plan_file(item, destination + "/" + item.relative_to(path).as_posix(), category)

    for value in profile["application_roots"]:
        plan_tree(value, "source/manual/" + value, "application")
    for pattern in profile["source_globs"]:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path.suffix.lower() == ".c":
                relative = path.relative_to(root).as_posix()
                destination = "source/manual/" + relative
                plan_file(path, destination, "application")
                if relative not in source_files and relative not in failed:
                    source_files.append(relative)
    if not source_files:
        issue("NO-C-SOURCES-MATCHED", "source_globs")
    for item in profile["includes"]:
        value = item["path"]
        dependency = item.get("dependency")
        destination = "dependencies/" + dependency if dependency else "source/manual/" + value
        plan_tree(value, destination, "dependency" if dependency else "application")
        for subdir in item.get("subdirectories", ["."]):
            relative = destination if subdir == "." else destination + "/" + subdir
            if not input_path(root, value + "/" + subdir).is_dir():
                issue("MISSING-INCLUDE-DIRECTORY", value + "/" + subdir)
            include_paths.append(relative)
            command_include_flags.extend(["-" + item["kind"], relative])
    sdk = profile["sysroot"]
    for subdir in sdk["include_subdirectories"]:
        plan_tree(sdk["path"] + "/" + subdir, "dependencies/sdk/" + subdir, "sdk")
    for relative in profile["evidence_files"]:
        path = input_path(root, relative)
        if path.is_file():
            plan_file(path, "provenance/" + relative, "evidence")
        else:
            issue("MISSING-EVIDENCE-FILE", relative)

    output.mkdir(parents=True)
    progress("Copying " + str(len(planned)) + " selected files...")
    # Keep existing but empty include directories valid after extraction.
    for relative in include_paths + ["dependencies/sdk/" + subdir for subdir in sdk["include_subdirectories"]]:
        (output / relative).mkdir(parents=True, exist_ok=True)
    for index, (path, destination, category) in enumerate(planned.values(), 1):
        if index % 500 == 0:
            progress("Copying files: " + str(index) + "/" + str(len(planned)))
        relative = path.relative_to(root).as_posix()
        try:
            target = output / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            if category == "evidence":
                collector.write_text(target, path.read_text(encoding="utf-8", errors="replace"))
            else:
                shutil.copyfile(path, target)
            copied.append({"source": relative, "output": destination, "category": category})
            copied_paths.add(relative)
        except OSError as error:
            failed[relative] = "copy_error_" + str(error.errno)
            issue("COPY-FAILED", relative)
    sources = ["source/manual/" + source for source in source_files if source in copied_paths]
    uncompiled_sources = sorted({
        item["output"] for item in copied
        if item["category"] == "application"
        and Path(item["output"]).suffix.lower() in collector.SOURCE_EXTENSIONS
        and item["output"] not in sources
    })
    commands = [{
        "directory": ".", "file": source,
        "arguments": [profile["compiler"], *profile["flags"], "--sysroot=dependencies/sdk",
                      *["-D" + value for value in profile["defines"]], *command_include_flags,
                      "-c", source],
        "provenance": PROVENANCE,
    } for source in sources]
    collector.write_json(output / "compilation/compile_commands.json", commands)
    collector.write_json(output / "provenance/reviewed-profile.json", profile)
    collector.write_json(output / "copy-mapping.json", {"copied_count": len(copied), "copied": copied})

    exclusions = []
    progress("Checking input coverage and exclusions...")
    for index, path in enumerate(root.rglob("*"), 1):
        if index % 2000 == 0:
            progress("Coverage: " + str(index) + " entries visited")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in copied_paths:
            continue
        reason = failed.get(relative, "outside_selected_sources_and_include_trees")
        if path.suffix.lower() == ".d" and relative not in failed:
            reason = "dependency_trace_not_required_for_analysis"
        exclusions.append({"source": relative, "reason": reason})
    collector.write_json(output / "copy-diagnostics.json", {"not_copied_count": len(exclusions), "not_copied": exclusions})
    collector.write_json(output / "copy-coverage.json", {
        "root_files": len(copied_paths) + len(exclusions), "copied_from_root_files": len(copied_paths),
        "not_copied_files": len(exclusions), "reason_counts": dict(Counter(item["reason"] for item in exclusions)),
        "path_bases": {"source": "ROOT", "output": "PACKAGE_ROOT"},
    })
    explicit_header = "cast-config/force-include-explicit.h"
    collector.write_text(output / explicit_header, "/* Explicit profile definitions only; compiler builtins unverified. */\n" +
                         "\n".join("#undef " + collector.macro_name(value) + "\n" + collector.define_line(value)
                                   for value in profile["defines"]) + "\n")
    profile_id = "profile-reviewed-sdk"
    collector.write_json(output / "cast-config/profile-reference.json", {
        "schema_version": 1, "provenance": PROVENANCE, "profiles": [{
            "profile_id": profile_id, "path_base": "PACKAGE_ROOT", "sources": sources,
            "includes": include_paths, "macros": profile["defines"], "force_include_candidate": explicit_header,
            "compiler": profile["compiler"], "flags": profile["flags"], "sysroot": "dependencies/sdk",
        }],
    })
    sdk_includes = ["dependencies/sdk/" + subdir for subdir in sdk["include_subdirectories"]]
    collector.write_text(output / "cast-config/cast-include-paths.relative.txt", "\n".join(include_paths + sdk_includes) + "\n")
    collector.write_json(output / "cast-config/single-analysis-unit.json", {
        "schema_version": 1, "status": "EXPLORATORY", "path_base": "PACKAGE_ROOT",
        "source_roots": ["source/manual"], "include_paths": include_paths,
        "sdk_include_candidates": sdk_includes, "sdk_include_status": "unverified_implicit_search_order",
        "force_include": "", "force_include_candidate": explicit_header,
        "force_include_status": "disabled_unverified_compiler_context",
        "profile_reference": "cast-config/profile-reference.json",
        "module_candidates": "cast-config/module-candidates.json",
        "module_creation_help": "cast-config/MODULE_CREATION_HELP.md",
    })
    candidates = collector.build_module_candidates([{"profile_id": profile_id, "sources": sources}])
    collector.write_json(output / "cast-config/module-candidates.json", candidates)
    collector.write_module_creation_help(output, candidates)
    collector.write_text(output / "CAST_ANALYSIS_PLAN.md", """# Analyse exploratoire Makefile / SDK

Ce ZIP est un package exploratoire autonome, sans qualification stricte.
Les commandes sont reconstruites depuis un profil JSON relu par un operateur.
Le Makefile et le script SDK ne sont ni interpretes ni executes.

1. Importer le ZIP dans Imaging et conserver l'AU C/C++ auto-creee.
2. Reporter les parametres de `cast-config/single-analysis-unit.json` manuellement.
3. Resoudre les chemins depuis la racine du package, y compris `directory = .`
   dans `compilation/compile_commands.json`. Ce JSON portable sert a la tracabilite.
4. Examiner les includes SDK candidats : leur ordre implicite est non verifie.
5. Le force-include candidat contient uniquement les macros explicites ; il reste
   desactive en attendant la revue du contexte preprocesseur.
6. Apres analyse complete, examiner les logs avant de creer les modules avec
   `cast-config/MODULE_CREATION_HELP.md` et `module-candidates.json`.

Lire `recovery-report.json` pour les limites et les erreurs de copie.
Le collecteur strict Conan/QCC existant ne qualifie pas ce package autonome.
""")
    report = {
        "schema_version": 1, "status": "EXPLORATORY", "strict_qualified": False,
        "provenance": PROVENANCE, "application": root.name, "architecture": profile["architecture"],
        "compile_commands": len(commands), "copied_files": len(copied),
        "sources_without_reconstructed_command": uncompiled_sources,
        "reservations": BASE_RESERVATIONS, "issues": issues,
        "conan_requirement": "not_assumed_by_this_profile", "qcc_requirement": "not_applicable_to_gcc_profile",
    }
    collector.write_json(output / "recovery-report.json", report)
    collector.write_json(output / "collection-status.json", {"status": "EXPLORATORY", "strict_qualified": False})
    if not issues:
        progress("Creating ZIP...")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for index, path in enumerate(output.rglob("*"), 1):
                if index % 500 == 0:
                    progress("Creating ZIP: " + str(index) + " entries visited")
                bundle.write(path, path.relative_to(output).as_posix() + ("/" if path.is_dir() else ""))
        progress("ZIP created: " + str(archive))
    else:
        progress("ZIP not created: " + str(len(issues)) + " issue(s); see " + str(output / "recovery-report.json"))
    print(json.dumps({"status": "EXPLORATORY", "compile_commands": len(commands), "issue_count": len(issues),
                      "archive_created": not issues}, indent=2))
    return 2 if issues else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Materialized application root")
    parser.add_argument("--profile", required=True, type=Path, help="Reviewed JSON context; no Make/shell evaluation")
    parser.add_argument("--output", required=True, type=Path, help="New package directory; ZIP is created alongside")
    args = parser.parse_args(argv)
    try:
        return recover(args)
    except (OSError, ValueError) as error:
        parser.exit(2, "Recovery failed: " + str(error) + "\n")


if __name__ == "__main__":
    sys.exit(main())
