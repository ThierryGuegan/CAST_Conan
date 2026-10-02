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


COMPILER_TOKEN_PATTERN = re.compile(
    r"(?P<compiler>(?:[^\s;&]+[\\/])?(?:myCMakeQCC\.bat|qcc(?:\.exe)?|q\+\+(?:\.exe)?|gcc(?:\.exe)?|g\+\+(?:\.exe)?))",
    re.IGNORECASE,
)
VARIABLE_PATTERN = re.compile(r"\$\(([^)]+)\)")
CONAN_CACHE_PATTERN = re.compile(
    r"(?P<root>.*?\.conan[/\\]data[/\\](?P<name>[^/\\]+)[/\\](?P<version>[^/\\]+)[/\\](?P<user>[^/\\]+)[/\\](?P<channel>[^/\\]+)[/\\]package[/\\](?P<package_id>[^/\\\s]+))",
    re.IGNORECASE,
)
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
COMPILE_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}
TRACE_SUFFIXES = {".rsp", ".response"}
DEPENDENCY_TRACE_SUFFIXES = {".d"}


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


def diagnostic_path(path, base):
    try:
        return relative_posix(path, base)
    except ValueError:
        return normalize_path_text(str(path))


def copy_file_checked(root, output, source, destination, diagnostics, copied_files, category):
    source_relative = diagnostic_path(source, root)
    destination_relative = diagnostic_path(destination, output)
    try:
        copy_file(source, destination)
    except OSError as error:
        diagnostics.append({
            "category": category,
            "source": source_relative,
            "destination": destination_relative,
            "reason": f"copy failed: {error}",
        })
        return False
    copied_files.append({
        "category": category,
        "source": source_relative,
        "destination": destination_relative,
    })
    return True


def file_count(path):
    return sum(1 for item in path.rglob("*") if item.is_file()) if path.is_dir() else 0


def directory_count(path):
    return sum(1 for item in path.rglob("*") if item.is_dir()) if path.is_dir() else 0


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


def path_is_under_any(path, roots):
    return any(is_relative_to(path, root) for root in roots)


def app_for_path(path, app_roots):
    matches = [app_root for app_root in app_roots if is_relative_to(path, app_root)]
    return max(matches, key=lambda item: len(item.parts), default=None)


def application_summary(root, app_root):
    build_root = app_root / "build"
    build_files = [item for item in build_root.rglob("*") if item.is_file()] if build_root.is_dir() else []
    source_files = application_source_files(app_root)
    return {
        "build_roots": [relative_posix(build_root, root)] if build_root.is_dir() else [],
        "build_files": len(build_files),
        "source_files": len(source_files),
        "build_make_files": len([item for item in build_files if item.name == "build.make"]),
        "flags_make_files": len([item for item in build_files if item.name == "flags.make"]),
        "dependency_files": len([item for item in build_files if item.name.endswith(".o.d")]),
    }


def application_source_files(app_root, compiled_sources=None, root=None):
    build_root = app_root / "build"
    result = []
    for item in app_root.rglob("*"):
        if not (
            item.is_file()
            and item.suffix.lower() in SOURCE_SUFFIXES
            and not (build_root.is_dir() and is_relative_to(item, build_root))
        ):
            continue
        if compiled_sources is not None and root is not None and item.suffix.lower() in COMPILE_SOURCE_SUFFIXES:
            recovered = "source/" + relative_posix(item, root)
            if recovered not in compiled_sources:
                continue
        result.append(item)
    return result


def compiled_source_set(compile_commands):
    return {
        command.get("file", "")
        for command in compile_commands
        if Path(str(command.get("file", ""))).suffix.lower() in COMPILE_SOURCE_SUFFIXES
    }


def conan_reference_parts(reference):
    base = reference.split("@", 1)[0]
    parts = base.split("/")
    if len(parts) < 2:
        return "", ""
    return parts[0], parts[1]


def conan_header_files(root, packages):
    return [item for item, _package in conan_header_entries(root, packages)]


def conan_cache_key(path, conan_root):
    try:
        parts = path.relative_to(conan_root).parts
    except ValueError:
        return None
    if len(parts) < 7 or parts[4] != "package":
        return None
    return parts[0], parts[1], parts[2], parts[3], parts[5]


def compiler_probe_files(root, app_roots):
    result = []
    seen = set()
    probe_roots = []
    root_compiler = root / "compiler"
    if root_compiler.is_dir():
        probe_roots.append(root_compiler)
    probe_roots.extend(app_roots)
    for probe_root in probe_roots:
        for item in probe_root.rglob("*"):
            if item.is_file() and (item.name.endswith(".macros.txt") or item.name.endswith(".includes.txt") or item.name == "qcc-variants.json"):
                if item in seen:
                    continue
                seen.add(item)
                result.append(item)
    return result


def compiler_probe_destination(root, output, path):
    relative = Path(relative_posix(path, root))
    if relative.parts and relative.parts[0] == "compiler":
        return output / relative
    return output / "compiler" / relative


def recovered_relative(path, root, output):
    try:
        return relative_posix(path, root)
    except ValueError:
        return relative_posix(path, output)


def compile_command_language(command):
    return "c++" if str(command.get("file", "")).lower().endswith((".cc", ".cpp", ".cxx", ".c++")) else "c"


def compile_command_variant(arguments):
    for index, argument in enumerate(arguments):
        if argument.startswith("-V") and len(argument) > 2:
            return argument[2:]
        if argument == "-V" and index + 1 < len(arguments):
            return arguments[index + 1]
    compiler = Path(arguments[0]).name if arguments else "qcc"
    return "unknown-qcc" if "qcc" in compiler.lower() else ""


def qcc_variants_from_compile_commands(compile_commands):
    variants = {}
    for command in compile_commands:
        arguments = command.get("arguments", [])
        language = compile_command_language(command)
        variant = compile_command_variant(arguments)
        if not variant:
            continue
        variants[(variant, language)] = {
            "variant": variant,
            "language": language,
            "macros_file": f"compiler/{probe_file_stem(variant)}-{language}.macros.txt",
            "include_search_file": f"compiler/{probe_file_stem(variant)}-{language}.includes.txt",
            "recovery": "reconstructed_from_compile_commands",
        }
    return [variants[key] for key in sorted(variants)]


def probe_file_stem(variant):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", variant).strip("._") or "unknown-qcc"


def probe_arguments(compile_commands, variant, language):
    arguments = []
    for command in compile_commands:
        command_language = compile_command_language(command)
        command_arguments = command.get("arguments", [])
        command_variant = compile_command_variant(command_arguments)
        if command_language == language and command_variant == variant:
            arguments.append(command_arguments)
    return arguments


def write_recovered_probe_files(output, compile_commands):
    variants = qcc_variants_from_compile_commands(compile_commands)
    if not variants:
        return []
    write_json(output / "compiler" / "qcc-variants.json", {"schema_version": 1, "variants": variants})
    created = [output / "compiler" / "qcc-variants.json"]
    for variant in variants:
        macros = output / variant["macros_file"]
        includes = output / variant["include_search_file"]
        macros.parent.mkdir(parents=True, exist_ok=True)
        includes.parent.mkdir(parents=True, exist_ok=True)
        defines = {}
        undefines = set()
        include_paths = []
        for arguments in probe_arguments(compile_commands, variant["variant"], variant["language"]):
            index = 0
            while index < len(arguments):
                argument = arguments[index]
                if argument in {"-D", "-U"} and index + 1 < len(arguments):
                    value = arguments[index + 1]
                    index += 2
                    if argument == "-D":
                        defines[value.split("=", 1)[0]] = value.split("=", 1)[1] if "=" in value else "1"
                    else:
                        undefines.add(value.split("=", 1)[0])
                    continue
                if argument.startswith("-D") and len(argument) > 2:
                    value = argument[2:]
                    defines[value.split("=", 1)[0]] = value.split("=", 1)[1] if "=" in value else "1"
                elif argument.startswith("-U") and len(argument) > 2:
                    undefines.add(argument[2:].split("=", 1)[0])
                elif argument in {"-I", "-isystem", "-iquote", "-idirafter"} and index + 1 < len(arguments):
                    include_paths.append(arguments[index + 1])
                    index += 1
                else:
                    for prefix in ("-I", "-isystem", "-iquote", "-idirafter"):
                        if argument.startswith(prefix) and len(argument) > len(prefix):
                            include_paths.append(argument[len(prefix):])
                            break
                index += 1
        macro_lines = ["/* Reconstructed from CMake flags.make and compile commands. */"]
        macro_lines.extend(f"#undef {name}" for name in sorted(undefines))
        macro_lines.extend(f"#define {name} {defines[name]}" for name in sorted(defines))
        include_lines = [
            "#include <...> search starts here:",
            *sorted(dict.fromkeys(include_paths)),
            "End of search list.",
        ]
        macros.write_text("\n".join(macro_lines) + "\n", encoding="utf-8")
        includes.write_text("\n".join(include_lines) + "\n", encoding="utf-8")
        created.extend([macros, includes])
    return created


def recovery_identity(root, args, app_roots, compile_commands):
    variants = qcc_variants_from_compile_commands(compile_commands)
    variant = variants[0]["variant"] if variants else "unknown"
    compiler = compile_commands[0]["arguments"][0] if compile_commands and compile_commands[0].get("arguments") else "unknown"
    application = args.application or root.name or "local-recovered"
    return {
        "schema_version": 1,
        "application": application,
        "application_version": "local-recovered",
        "git_commit": "unknown-local-recovery",
        "pipeline_id": "makefile_local_recover",
        "target": {
            "label": "local-recovered",
            "os": "unknown",
            "architecture": "unknown",
            "build_type": "recovered",
        },
        "compiler": {
            "driver": compiler,
            "variant": variant,
        },
        "conan": {
            "host_profile": "recovered-host",
            "build_profile": "recovered-build",
        },
        "paths": {
            "source_root": "source",
            "generated_root": "generated",
        },
        "recovery": {
            "tool": "makefile_local_recover.py",
            "root": ".",
            "applications": [app.name for app in app_roots],
        },
    }


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


def recover_compile_commands(root, build_root, packages):
    entries = []
    for build_make in sorted(build_root.rglob("build.make")):
        flags_make = build_make.parent / "flags.make"
        if not flags_make.is_file():
            continue
        variables = parse_make_variables(flags_make)
        build_dir = ""
        source_dir = ""
        for raw in build_make.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw.startswith("CMAKE_BINARY_DIR ="):
                build_dir = raw.split("=", 1)[1].strip()
            if raw.startswith("CMAKE_SOURCE_DIR ="):
                source_dir = raw.split("=", 1)[1].strip()
            match = COMPILER_TOKEN_PATTERN.search(raw)
            if not match:
                continue
            command_text = raw[match.start():]
            if not re.search(r"\s-c(?:\s|$)", command_text):
                continue
            expanded = expand_variables(command_text, variables)
            arguments = parse_command(expanded)
            source_index = compile_source_index(arguments)
            if source_index is None:
                continue
            if arguments:
                arguments[0] = Path(arguments[0]).name
            arguments = normalize_compile_arguments(root, build_root, source_dir, packages, arguments, source_index)
            recovered_source = normalize_compile_path(root, build_root, source_dir, packages, arguments[source_index], option="-c")
            entries.append({
                "directory": relative_posix(build_root, root) if build_dir else relative_posix(build_make.parent, root),
                "file": recovered_source,
                "arguments": arguments,
            })
    return entries


def is_compile_source_argument(value):
    return Path(str(value).strip("\"'")).suffix.lower() in COMPILE_SOURCE_SUFFIXES


def compile_source_index(arguments):
    source_index = None
    for index in range(len(arguments) - 1):
        if arguments[index] == "-c" and is_compile_source_argument(arguments[index + 1]):
            source_index = index + 1
    if source_index is not None:
        return source_index
    for index in range(len(arguments) - 1, -1, -1):
        if is_compile_source_argument(arguments[index]):
            return index
    return None


def normalize_compile_arguments(root, build_root, source_dir, packages, arguments, source_index):
    result = []
    paired_options = {"-I", "-isystem", "-iquote", "-idirafter", "-include", "-imacros", "--sysroot", "-isysroot", "-MF", "-MT", "-MQ", "-o"}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if index == source_index:
            result.append(normalize_compile_path(root, build_root, source_dir, packages, argument, option="-c"))
            index += 1
            continue
        if argument in paired_options and index + 1 < len(arguments):
            result.append(argument)
            result.append(normalize_compile_path(root, build_root, source_dir, packages, arguments[index + 1], option=argument))
            index += 2
            continue
        handled = False
        for prefix in ("--sysroot=", "-isysroot=", "-isystem=", "-iquote=", "-idirafter=", "-include=", "-imacros=", "-I", "-isystem", "-iquote", "-idirafter", "-include", "-imacros"):
            if argument.startswith(prefix) and argument != prefix:
                result.append(prefix + normalize_compile_path(root, build_root, source_dir, packages, argument[len(prefix):], option=prefix.rstrip("=")))
                handled = True
                break
        if not handled:
            result.append(argument)
        index += 1
    return result


def normalize_compile_path(root, build_root, source_dir, packages, value, option=""):
    if option == "-c":
        return recover_source_path(root, build_root, source_dir, value)
    if value.startswith("=/"):
        return "sysroot-relative/" + value[2:].lstrip("/")
    conan = recover_conan_path(packages, value)
    if conan:
        return conan
    local = recover_local_path(root, value)
    if local:
        return "source/" + local
    if not looks_absolute(value):
        return value
    mapped = recover_source_path(root, build_root, source_dir, value)
    if mapped != value:
        return mapped
    return unresolved_relative_path(value)


def recover_source_path(root, build_root, source_dir, source):
    normalized_source = source.replace("\\", "/")
    normalized_source_dir = source_dir.replace("\\", "/").rstrip("/")
    app_root = build_root.parent
    if normalized_source_dir and normalized_source.startswith(normalized_source_dir + "/"):
        suffix = normalized_source[len(normalized_source_dir) + 1:]
        candidate = app_root / suffix
        if candidate.exists():
            return "source/" + relative_posix(candidate, root)
    candidate = Path(source)
    if candidate.is_absolute() and is_relative_to(candidate, root):
        return "source/" + relative_posix(candidate, root)
    if not candidate.is_absolute():
        local_candidate = app_root / candidate
        if local_candidate.exists():
            return "source/" + relative_posix(local_candidate, root)
    return unresolved_relative_path(source) if looks_absolute(source) else source


def recover_conan_path(packages, value):
    normalized = normalize_path_text(value)
    parsed_value = package_from_cache_path(normalized)
    if parsed_value:
        package = find_package_for_conan_path(packages, parsed_value)
        if package:
            return package_exported_path(package, parsed_value, normalized)
        return conan_export_recovered_path(parsed_value, normalized)
    for package in packages:
        roots = [package.get("original_root", "")]
        roots.extend(package.get("recovered_from_roots", []))
        for root_value in roots:
            original = normalize_path_text(root_value)
            if not original:
                continue
            if normalized == original:
                suffix = ""
            elif normalized.startswith(original + "/"):
                suffix = normalized[len(original) + 1:]
            else:
                continue
            parsed = package_from_cache_path(original)
            if parsed:
                return package_exported_path(package, parsed, normalized)
            reference = package.get("reference", "unknown/unknown")
            package_id = package.get("package_id", "unknown")
            base = f"conan/export-recovered/{reference}/{package_id}"
            return base + (("/" + suffix) if suffix else "")
    return ""


def find_package_for_conan_path(packages, parsed):
    reference = f"{parsed['name']}/{parsed['version']}"
    canonical_root = parsed["canonical_root"]
    for package in packages:
        if package.get("original_root") == canonical_root or package.get("resolved_root") == canonical_root:
            return package
    if parsed["user"] == "*" or parsed["channel"] == "*":
        candidates = [
            package for package in packages
            if package.get("reference") == reference and package.get("package_id") == parsed["package_id"]
        ]
        if len(candidates) == 1:
            return candidates[0]
    candidates = [
        package for package in packages
        if (
            package.get("reference") == reference
            and package.get("package_id") == parsed["package_id"]
            and not package.get("original_root")
        )
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def package_exported_path(package, parsed, value):
    normalized = normalize_path_text(value)
    root = normalize_path_text(parsed["root"])
    suffix = ""
    if normalized.startswith(root + "/"):
        suffix = normalized[len(root) + 1:]
    base = package.get("exported_root") or ("conan/export-recovered/" + parsed["canonical_root"])
    return base + (("/" + suffix) if suffix else "")


def conan_export_recovered_path(parsed, value):
    normalized = normalize_path_text(value)
    root = normalize_path_text(parsed["root"])
    suffix = ""
    if normalized.startswith(root + "/"):
        suffix = normalized[len(root) + 1:]
    base = "conan/export-recovered/" + parsed["canonical_root"]
    return base + (("/" + suffix) if suffix else "")


def canonical_conan_root(parsed):
    return (
        f".conan/data/{parsed['name']}/{parsed['version']}/"
        f"{parsed['user']}/{parsed['channel']}/package/{parsed['package_id']}"
    )


def conan_recovered_header_source(root, package):
    parsed = package_from_cache_path(package.get("resolved_root", "") or package.get("original_root", ""))
    return root / parsed["canonical_root"] if parsed else None


def package_matches_conan_header(path, conan_root, package):
    key = conan_cache_key(path, conan_root)
    if not key:
        return False
    name, version, user, channel, package_id = key
    reference_name, reference_version = conan_reference_parts(package.get("reference", ""))
    if name != reference_name or version != reference_version or package_id != package.get("package_id", ""):
        return False
    parsed = package_from_cache_path(package.get("resolved_root", "") or package.get("original_root", ""))
    if not parsed:
        return True
    if parsed["user"] in {"*", user} and parsed["channel"] in {"*", channel}:
        return True
    return False


def conan_header_entries(root, packages):
    conan_root = root / ".conan" / "data"
    if not conan_root.is_dir():
        return []
    entries = []
    for item in conan_root.rglob("*"):
        if not (
            item.is_file()
            and "include" in item.parts
            and item.suffix.lower() in SOURCE_SUFFIXES
        ):
            continue
        for package in packages:
            if package_matches_conan_header(item, conan_root, package):
                entries.append((item, package))
                break
    return entries


def conan_package_copy_gaps(root, packages, copied_entries):
    copied_keys = {
        (package.get("reference", ""), package.get("package_id", ""))
        for _item, package in copied_entries
    }
    gaps = []
    seen_gaps = set()
    conan_root = root / ".conan" / "data"
    for package in packages:
        key = (package.get("reference", ""), package.get("package_id", ""))
        if key in copied_keys:
            continue
        if key in seen_gaps:
            continue
        seen_gaps.add(key)
        expected_root = package.get("resolved_root") or package.get("original_root") or package.get("reference", "")
        if not conan_root.is_dir():
            reason = "delivered .conan/data directory not found"
        elif package.get("resolved_root"):
            reason = "resolved Conan package root contains no supported header files"
        elif package.get("original_root"):
            reason = "Conan package root not found in delivered .conan cache"
        else:
            reason = "Conan root path unavailable in recovered metadata"
        gaps.append({
            "category": "conan_headers",
            "source": expected_root,
            "destination": package.get("exported_root", ""),
            "reason": reason,
            "reference": package.get("reference", ""),
            "package_id": package.get("package_id", ""),
        })
    return gaps


def conan_header_destination(root, output, item, package):
    parsed = package_from_cache_path(normalize_path_text(str(item)))
    package_root = conan_package_root_from_header(item)
    if parsed and package_root and is_relative_to(item, package_root):
        return output / "conan" / "export-recovered" / parsed["canonical_root"] / relative_posix(item, package_root)
    source_root = conan_recovered_header_source(root, package)
    if source_root and is_relative_to(item, source_root):
        return output / "conan" / "export-recovered" / package["original_root"] / relative_posix(item, source_root)
    return output / "conan" / "export-recovered" / package["original_root"] / item.name


def conan_package_root_from_header(item):
    parts = item.parts
    for index, part in enumerate(parts):
        if part == "package" and index + 1 < len(parts):
            return Path(*parts[:index + 2])
    return None


def increment_reason(reasons, reason, path=None, root=None, samples_limit=20):
    entry = reasons.setdefault(reason, {"count": 0, "samples": []})
    entry["count"] += 1
    if path is not None and root is not None and len(entry["samples"]) < samples_limit:
        entry["samples"].append(relative_posix(path, root))


def classify_uncopied_root_file(path, root, selected_app_roots, all_app_roots, packages):
    app_root = app_for_path(path, all_app_roots)
    selected_app_root = app_for_path(path, selected_app_roots)
    if app_root and not selected_app_root:
        return "application_not_selected"
    if selected_app_root:
        for folder_name in ("src", "include"):
            folder = selected_app_root / folder_name
            if folder.is_dir() and is_relative_to(path, folder):
                if path.suffix.lower() in SOURCE_SUFFIXES:
                    return "selected_source_or_header_expected_but_not_copied"
                return "source_or_include_file_extension_not_recovered"
        build_root = selected_app_root / "build"
        if build_root.is_dir() and is_relative_to(path, build_root):
            if path.suffix.lower() in DEPENDENCY_TRACE_SUFFIXES:
                return "dependency_trace_not_copied_optional_absolute_paths"
            if path.suffix.lower() in TRACE_SUFFIXES:
                return "build_trace_expected_but_not_copied"
            return "build_artifact_not_required_for_recovery"
        if path.suffix.lower() in SOURCE_SUFFIXES:
            return "selected_source_or_header_expected_but_not_copied"
        return "selected_application_file_outside_recovered_roots"
    conan_data = root / ".conan" / "data"
    if conan_data.is_dir() and is_relative_to(path, conan_data):
        if path.suffix.lower() in SOURCE_SUFFIXES and "include" in path.parts:
            return "conan_header_not_referenced_by_recovered_packages"
        return "conan_cache_non_header_or_binary_artifact"
    compiler_root = root / "compiler"
    if compiler_root.is_dir() and is_relative_to(path, compiler_root):
        return "compiler_directory_non_probe_file"
    return "root_file_outside_recovery_scope"


def build_copy_coverage(root, output, selected_app_roots, copied_files, not_copied, packages):
    copied_sources = {item["source"] for item in copied_files}
    all_app_roots = discover_applications(root)
    reasons = {}
    root_files = [item for item in root.rglob("*") if item.is_file()]
    for item in root_files:
        relative = relative_posix(item, root)
        if relative in copied_sources:
            increment_reason(reasons, "copied_from_root", item, root)
            continue
        reason = classify_uncopied_root_file(item, root, selected_app_roots, all_app_roots, packages)
        increment_reason(reasons, reason, item, root)
    output_files = [item for item in output.rglob("*") if item.is_file()]
    generated_output_files = []
    copied_destinations = {item["destination"] for item in copied_files}
    for item in output_files:
        relative = relative_posix(item, output)
        if relative not in copied_destinations:
            generated_output_files.append(relative)
    return {
        "schema_version": 1,
        "root": ".",
        "output": ".",
        "root_files": len(root_files),
        "root_directories": directory_count(root),
        "output_files_at_report_time": len(output_files),
        "output_directories_at_report_time": directory_count(output),
        "copied_from_root_files": len(copied_files),
        "not_copied_expected_entries": len(not_copied),
        "generated_output_files": len(generated_output_files),
        "generated_output_samples": sorted(generated_output_files)[:50],
        "reason_counts": {
            reason: data["count"]
            for reason, data in sorted(reasons.items())
        },
        "reason_samples": {
            reason: data["samples"]
            for reason, data in sorted(reasons.items())
        },
        "notes": [
            "Every source path is relative to --root.",
            "Every output path is relative to --output.",
            "Most count differences are expected: only CAST-relevant source/header files, selected build traces, selected Conan headers and compiler probes are copied.",
            "Dependency .d files are not copied by local recovery because they are optional traceability inputs and often preserve absolute build paths.",
            "Generated output files are recovery metadata produced by the script and therefore do not map back to a --root file.",
        ],
    }


def recover_local_path(root, value):
    retry_value = value
    normalized_value = normalize_path_text(value)
    if normalized_value.startswith("unresolved/"):
        retry_value = normalized_value[len("unresolved/"):]
    candidate = Path(retry_value)
    if candidate.is_absolute() and is_relative_to(candidate, root):
        return relative_posix(candidate, root)
    normalized = normalize_path_text(retry_value)
    parts = [part for part in normalized.split("/") if part and part != "."]
    applications = {app.name: app for app in discover_applications(root)}
    for index, part in enumerate(parts):
        app_root = applications.get(part)
        if not app_root:
            continue
        local = app_root.joinpath(*parts[index + 1:])
        if local.exists():
            return relative_posix(local, root)
    return ""


def normalize_path_text(value):
    return value.replace("\\", "/").rstrip("/")


def looks_absolute(value):
    normalized = normalize_path_text(value)
    return normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized) is not None


def unresolved_relative_path(value):
    normalized = normalize_path_text(value)
    normalized = re.sub(r"^[A-Za-z]:/", "", normalized)
    normalized = normalized.lstrip("/")
    return "unresolved/" + normalized


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
        "user": match.group("user"),
        "channel": match.group("channel"),
        "package_id": match.group("package_id"),
        "root": match.group("root").replace("\\", "/"),
        "canonical_root": canonical_conan_root(match.groupdict()),
    }


def resolve_local_conan_root(root, parsed):
    conan_root = root / ".conan" / "data" / parsed["name"] / parsed["version"]
    if not conan_root.is_dir():
        return ""
    exact = root / parsed["canonical_root"]
    if exact.is_dir():
        return parsed["canonical_root"]
    for candidate in conan_root.glob(f"*/*/package/{parsed['package_id']}"):
        if candidate.is_dir():
            return ".conan/data/" + relative_posix(candidate, root / ".conan" / "data")
    return ""


def package_roots(root, rootpath):
    parsed = package_from_cache_path(rootpath) if rootpath else None
    if not parsed:
        return rootpath, "", rootpath, None
    original_root = parsed["canonical_root"]
    resolved_root = resolve_local_conan_root(root, parsed)
    exported_root = "conan/export-recovered/" + (resolved_root or original_root)
    return original_root, resolved_root, exported_root, parsed


def package_manifest_key(package):
    return (
        package.get("reference", ""),
        package.get("recipe_revision", ""),
        package.get("context", ""),
        package.get("package_id", ""),
        package.get("package_revision", ""),
    )


def is_manifest_exportable_package(package):
    if package.get("context") != "host":
        return True
    return bool(package.get("original_root") and package.get("exported_root"))


def dedupe_recovered_packages(packages):
    result = []
    seen = set()
    for package in packages:
        if not is_manifest_exportable_package(package):
            continue
        key = package_manifest_key(package)
        if key in seen:
            continue
        seen.add(key)
        result.append(package)
    return result


def package_identity_key(package):
    return package.get("reference", ""), package.get("package_id", "")


def conan_manifest_packages_with_headers(packages, conan_headers):
    keys_with_headers = {
        package_identity_key(package)
        for _item, package in conan_headers
    }
    return [
        package for package in packages
        if package_identity_key(package) in keys_with_headers
    ]


def recover_conan(root, build_root):
    conaninfo = first_existing(build_root, ["conaninfo.txt"])
    conanbuildinfo = first_existing(build_root, ["conanbuildinfo.txt"])
    requires = parse_conaninfo(conaninfo) if conaninfo else {}
    rootpaths = parse_rootpaths(conanbuildinfo) if conanbuildinfo else {}
    packages = []
    seen = set()

    for reference, package_id in requires.items():
        name = reference.split("/", 1)[0]
        rootpath = rootpaths.get(name, "")
        original_root, resolved_root, exported_root, parsed = package_roots(root, rootpath)
        canonical_root = parsed["canonical_root"] if parsed else rootpath
        key = (reference, package_id)
        seen.add(key)
        packages.append({
            "reference": reference,
            "recipe_revision": "",
            "context": "host",
            "package_id": package_id,
            "package_revision": "",
            "original_root": original_root,
            "logical_root": f"conan://{reference}/{package_id}",
            "exported_root": exported_root if original_root else "",
            "recovery": "headers_not_available",
            "cache_name": parsed["name"] if parsed else name,
            "resolved_root": resolved_root,
            "recovered_from_roots": [rootpath] if parsed and rootpath != original_root else [],
        })

    for name, rootpath in rootpaths.items():
        original_root, resolved_root, exported_root, parsed = package_roots(root, rootpath)
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
            "original_root": original_root,
            "logical_root": f"conan://{reference}/{parsed['package_id']}",
            "exported_root": exported_root,
            "recovery": "headers_not_available",
            "cache_name": name,
            "resolved_root": resolved_root,
            "recovered_from_roots": [rootpath] if rootpath != original_root else [],
        })
    return packages


def copy_recovery_inputs(root, app_roots, output, packages, compile_commands=None):
    counts = {
        "copied_source_files": 0,
        "copied_conan_header_files": 0,
        "copied_build_traces": 0,
        "copied_probe_files": 0,
    }
    not_copied = []
    copied_files = []
    compiled_sources = compiled_source_set(compile_commands or []) if compile_commands else None
    for app_root in app_roots:
        for item in application_source_files(app_root, compiled_sources, root):
            destination = output / "source" / relative_posix(item, root)
            if copy_file_checked(root, output, item, destination, not_copied, copied_files, "source"):
                counts["copied_source_files"] += 1
        build_root = app_root / "build"
        if build_root.is_dir():
            for item in build_root.rglob("*"):
                if item.is_file() and item.suffix.lower() in TRACE_SUFFIXES:
                    destination = output / "build" / relative_posix(item, root)
                    if copy_file_checked(root, output, item, destination, not_copied, copied_files, "build_trace"):
                        counts["copied_build_traces"] += 1

    conan_entries = conan_header_entries(root, packages)
    for item, package in conan_entries:
        destination = conan_header_destination(root, output, item, package)
        if copy_file_checked(root, output, item, destination, not_copied, copied_files, "conan_headers"):
            counts["copied_conan_header_files"] += 1
    not_copied.extend(conan_package_copy_gaps(root, packages, conan_entries))

    for item in compiler_probe_files(root, app_roots):
        destination = compiler_probe_destination(root, output, item)
        if copy_file_checked(root, output, item, destination, not_copied, copied_files, "compiler_probe"):
            counts["copied_probe_files"] += 1

    return counts, not_copied, copied_files


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
    summaries = {app.name: application_summary(root, app) for app in app_roots}
    build_roots = [app / "build" for app in app_roots if (app / "build").is_dir()]

    compile_commands = []
    packages = []
    for build_root in build_roots:
        recovered_packages = recover_conan(root, build_root)
        packages.extend(recovered_packages)
        compile_commands.extend(recover_compile_commands(root, build_root, recovered_packages))
    packages = dedupe_recovered_packages(packages)

    conan_headers = conan_header_files(root, packages)
    conan_entries = conan_header_entries(root, packages)
    manifest_packages = conan_manifest_packages_with_headers(packages, conan_entries)
    probes = compiler_probe_files(root, app_roots)
    copied, not_copied, copied_files = copy_recovery_inputs(root, app_roots, output, packages, compile_commands)
    recovered_probe_placeholders = []
    if not probes and compile_commands:
        recovered_probe_placeholders = write_recovered_probe_files(output, compile_commands)
        probes = recovered_probe_placeholders
    if compile_commands:
        write_json(output / "compilation" / "compile_commands.json", compile_commands)
    write_json(output / "identity" / "BUILD_IDENTITY.json", recovery_identity(root, args, app_roots, compile_commands))
    if manifest_packages:
        conan_manifest = {"schema_version": 1, "packages": manifest_packages}
        conan_graph = {
            "recovery": "partial_conan1_metadata",
            "packages": [{"ref": item["reference"], "context": item["context"], "package_id": item["package_id"]} for item in manifest_packages],
        }
        write_json(output / "conan" / "packages.json", conan_manifest)
        write_json(output / "conan" / "packages.recovered.json", conan_manifest)
        write_json(output / "conan" / "conan-graph.json", conan_graph)
        write_json(output / "conan" / "conan-graph.recovered.json", conan_graph)
    write_json(output / "copy-diagnostics.json", {
        "schema_version": 1,
        "not_copied_count": len(not_copied),
        "not_copied": not_copied,
    })
    write_json(output / "copy-mapping.json", {
        "schema_version": 1,
        "copied_count": len(copied_files),
        "copied": copied_files,
    })

    write_json(output / "root-build-files.json", {
        "root": ".",
        "application_filter": args.application,
        "applications_detected": [app.name for app in app_roots],
        "applications": summaries,
        "build_roots": [relative_posix(path, root) for path in build_roots],
        "conan_header_files": [relative_posix(path, root) for path in conan_headers],
        "compiler_probe_files": [recovered_relative(path, root, output) for path in probes],
    })
    copy_coverage = build_copy_coverage(root, output, app_roots, copied_files, not_copied, packages)
    write_json(output / "copy-coverage.json", copy_coverage)

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
        "root": ".",
        "output": ".",
        "application_filter": args.application,
        "applications_detected": [app.name for app in app_roots],
        "applications_count": len(app_roots),
        "applications": summaries,
        "build_roots": len(build_roots),
        "conan_header_files": len(conan_headers),
        "compiler_probe_files": len(probes),
        "recovered_probe_placeholders": len(recovered_probe_placeholders),
        "compile_commands": len(compile_commands),
        "conan_packages": len(manifest_packages),
        "not_copied_files": len(not_copied),
        "copied_mapping_files": len(copied_files),
        "root_files": copy_coverage["root_files"],
        "root_directories": copy_coverage["root_directories"],
        "output_files_at_coverage_report_time": copy_coverage["output_files_at_report_time"],
        "output_directories_at_coverage_report_time": copy_coverage["output_directories_at_report_time"],
        "generated_output_files_at_coverage_report_time": copy_coverage["generated_output_files"],
        **copied,
        "missing_for_strict_cast": missing,
        "notes": [
            "Recovery scans a materialized root directory directly; it does not read text listings or archives.",
            "Use --application only when a single module must be isolated; omit it to process every detected application.",
            "Recovered compile_commands.json is reconstructed from CMake build.make and flags.make when those files are present.",
            "Recovered Conan metadata is inferred from Conan 1 text files and may still require review before strict CAST use.",
            "When compiler probes are absent but QCC variants are visible in compile commands, macro/include files are reconstructed from the observed compiler flags.",
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
