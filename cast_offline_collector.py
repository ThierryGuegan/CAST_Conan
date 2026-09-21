#!/usr/bin/env python3
"""Hardened offline collector for CAST Imaging C/C++ analysis.

The collector never invokes Conan, Make, GCC, QCC, Git, Matlab, or a network
client. Build-dependent facts must be exported by the client CI environment.
Python 3.9+ standard library only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


COLLECTOR_VERSION = "2.1.0"
MAX_RESPONSE_FILE_BYTES = 16 * 1024 * 1024
MAX_PROBE_FILE_BYTES = 64 * 1024 * 1024
HEADER_EXTENSIONS = {
    ".h", ".hh", ".hpp", ".hxx", ".inc", ".inl", ".ipp", ".tcc",
    ".tpp", ".def", ".cfg", ".config",
}
SOURCE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".c++", ".py"}
SKIP_NAMES = {".git", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache"}
TEXT_EXTENSIONS = {
    ".txt", ".log", ".json", ".lock", ".mk", ".cmake", ".properties",
    ".rsp", ".response", ".cfg", ".conf", ".ini", ".yaml", ".yml",
}
REQUIRED_IDENTITY_KEYS = {
    "Application", "GitCommit", "TargetLabel", "TargetOS",
    "TargetArchitecture", "Compiler", "CompilerVariant", "BuildType",
    "HostProfile", "BuildProfile", "BuildPipeline",
}
SECRET_PATTERNS = [
    ("credential-assignment", re.compile(r"(?i)\b(password|passwd|token|secret|api[_-]?key|proxyPassword)\b\s*[:=]\s*[^\s,;]+")),
    ("authorization-bearer", re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+\S+")),
    ("password-argument", re.compile(r"(?i)(--password|--token|--secret)\s+\S+")),
    ("credential-url", re.compile(r"(?i)https?://[^\s:/]+:[^@\s]+@")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]
ANALYSIS_PATTERNS = {
    "missing_headers": re.compile(r"(?i)(file not found|cannot open include file|no such file or directory).*\.(h|hpp|hh|hxx)"),
    "preprocessor_errors": re.compile(r"(?i)(preprocess|preprocessor|#error).*\b(error|failed|failure)\b"),
    "parser_errors": re.compile(r"(?i)(parse|parser|syntax).*\b(error|failed|failure)\b"),
    "unknown_symbols": re.compile(r"(?i)\b(unknown|undefined)\s+(type|macro|identifier)\b"),
}


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._-") or "unknown"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


class Audit:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.issues: List[Dict[str, str]] = []

    def add(self, severity: str, code: str, message: str, path: str = "") -> None:
        self.issues.append({"severity": severity, "code": code, "message": message, "path": path})

    def critical(self, code: str, message: str, path: str = "") -> None:
        self.add("CRITICAL", code, message, path)

    def warning(self, code: str, message: str, path: str = "") -> None:
        self.add("WARNING", code, message, path)

    def info(self, code: str, message: str, path: str = "") -> None:
        self.add("INFO", code, message, path)

    @property
    def critical_count(self) -> int:
        return sum(1 for issue in self.issues if issue["severity"] == "CRITICAL")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue["severity"] == "WARNING")

    def status(self) -> str:
        if self.critical_count:
            return "NOT_QUALIFIED" if self.mode == "strict" else "EXPLORATORY"
        return "READY_FOR_ANALYSIS" if self.mode == "strict" else "EXPLORATORY"


def redact(text: str) -> str:
    redacted = text
    for _, pattern in SECRET_PATTERNS:
        redacted = pattern.sub("<REDACTED>", redacted)
    return redacted


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def safe_input_path(root: Path, relative: str, audit: Audit, code: str) -> Optional[Path]:
    candidate = (root / relative).resolve()
    if not is_relative_to(candidate, root.resolve()):
        audit.critical(code, "Path escapes the CLIENT_DROP root", relative)
        return None
    return candidate


def scan_input_tree(root: Path, audit: Audit, max_files: int, max_bytes: int) -> Dict[str, int]:
    file_count = 0
    total_bytes = 0
    symlinks = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_dirs = []
        for dirname in dirs:
            path = current_path / dirname
            if dirname in SKIP_NAMES:
                continue
            if path.is_symlink():
                symlinks += 1
                audit.critical("SEC-SYMLINK", "Symbolic-link directories are not accepted", str(path.relative_to(root)))
                continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs
        for filename in files:
            path = current_path / filename
            if path.is_symlink():
                symlinks += 1
                audit.critical("SEC-SYMLINK", "Symbolic-link files are not accepted", str(path.relative_to(root)))
                continue
            if not path.is_file():
                audit.warning("INPUT-SPECIAL-FILE", "Non-regular input skipped", str(path.relative_to(root)))
                continue
            file_count += 1
            try:
                total_bytes += path.stat().st_size
            except OSError:
                audit.critical("INPUT-STAT", "Unable to read input file metadata", str(path.relative_to(root)))
    if file_count > max_files:
        audit.critical("INPUT-FILE-LIMIT", f"Input contains {file_count} files; limit is {max_files}")
    if total_bytes > max_bytes:
        audit.critical("INPUT-SIZE-LIMIT", f"Input size is {total_bytes} bytes; limit is {max_bytes}")
    return {"file_count": file_count, "total_bytes": total_bytes, "symlink_count": symlinks}


def read_properties(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def flatten_identity_json(data: Dict[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(f"{prefix}.{key}" if prefix else str(key), child)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[prefix] = "" if value is None else str(value)

    walk("", data)
    aliases = {
        "application": "Application", "application_version": "ApplicationVersion",
        "git_commit": "GitCommit", "pipeline_id": "BuildPipeline",
        "job_id": "BuildJob", "target.label": "TargetLabel",
        "target.os": "TargetOS", "target.os_version": "TargetOSVersion",
        "target.architecture": "TargetArchitecture", "target.build_type": "BuildType",
        "compiler.driver": "Compiler", "compiler.version": "CompilerVersion",
        "compiler.variant": "CompilerVariant", "conan.version": "ConanVersion",
        "conan.host_profile": "HostProfile", "conan.build_profile": "BuildProfile",
        "conan.lockfile_sha256": "ConanLockfileSHA256",
        "paths.source_root": "SourceRoot", "paths.generated_root": "GeneratedRoot",
        "paths.qnx_target": "QNX_TARGET", "generated_code.version": "MatlabVersion",
        "generated_code.generation_id": "GeneratedCodeID",
        "generated_code.source_model_commit": "GeneratedSourceCommit",
    }
    normalized = dict(result)
    for source, destination in aliases.items():
        if source in result:
            normalized[destination] = result[source]
    return normalized


def load_identity(raw_root: Path, audit: Audit) -> Tuple[Dict[str, str], Dict[str, Any]]:
    candidates = [
        raw_root / "identity" / "BUILD_IDENTITY.json",
        raw_root / "BUILD_IDENTITY.json",
        raw_root / "client-build.json",
    ]
    structured: Dict[str, Any] = {}
    metadata: Dict[str, str] = {}
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            audit.critical("IDENTITY-JSON", f"Invalid identity JSON: {exc}", str(path.relative_to(raw_root)))
            break
        if not isinstance(data, dict):
            audit.critical("IDENTITY-SCHEMA", "Identity JSON root must be an object", str(path.relative_to(raw_root)))
            break
        if data.get("schema_version") != 1:
            audit.critical("IDENTITY-SCHEMA-VERSION", "BUILD_IDENTITY.json schema_version must be 1", str(path.relative_to(raw_root)))
        for object_key in ("target", "compiler", "conan", "paths"):
            if not isinstance(data.get(object_key), dict):
                audit.critical("IDENTITY-SCHEMA", f"Identity field must be an object: {object_key}", str(path.relative_to(raw_root)))
        structured = data
        metadata = flatten_identity_json(data)
        break
    if not metadata:
        metadata = read_properties(raw_root / "client-build.properties")
        structured = {"legacy_properties": metadata}
        if metadata:
            audit.warning("IDENTITY-LEGACY", "Legacy properties identity used; JSON identity is recommended")
    if not metadata:
        audit.critical("IDENTITY-MISSING", "No BUILD_IDENTITY.json or client-build.properties supplied")
    for key in sorted(REQUIRED_IDENTITY_KEYS):
        if not metadata.get(key):
            audit.critical("IDENTITY-REQUIRED", f"Required identity field is missing: {key}")
    generated_commit = metadata.get("GeneratedSourceCommit")
    if generated_commit and metadata.get("GitCommit") and generated_commit != metadata["GitCommit"]:
        audit.critical("IDENTITY-GENERATED-COMMIT", "Generated code commit differs from application GitCommit")
    return metadata, structured


def parse_sha256_manifest(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.match(r"^([0-9a-fA-F]{64})\s+[* ]?(.+)$", line)
        if match:
            values[match.group(2).strip()] = match.group(1).lower()
    return values


def verify_input_manifest(raw_root: Path, audit: Audit) -> Dict[str, Any]:
    candidates = [raw_root / "FILES.sha256", raw_root / "identity" / "FILES.sha256"]
    manifest = next((path for path in candidates if path.exists()), None)
    result: Dict[str, Any] = {"manifest": "", "listed": 0, "verified": 0, "missing": [], "mismatched": [], "unlisted": []}
    if manifest is None:
        audit.critical("MANIFEST-MISSING", "FILES.sha256 is required")
        return result
    result["manifest"] = str(manifest.relative_to(raw_root))
    entries = parse_sha256_manifest(manifest)
    nonempty_lines = [line for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if len(nonempty_lines) != len(entries):
        audit.critical("MANIFEST-SYNTAX", "FILES.sha256 contains malformed or duplicate entries", result["manifest"])
    result["listed"] = len(entries)
    if not entries:
        audit.critical("MANIFEST-EMPTY", "FILES.sha256 contains no valid entries", result["manifest"])
        return result
    for relative, expected in entries.items():
        path = safe_input_path(raw_root, relative, audit, "MANIFEST-PATH")
        if path is None:
            continue
        if not path.is_file() or path.is_symlink():
            result["missing"].append(relative)
            audit.critical("MANIFEST-MISSING-FILE", "Manifest entry is missing or is not a regular file", relative)
            continue
        actual = sha256(path)
        if actual != expected:
            result["mismatched"].append(relative)
            audit.critical("MANIFEST-HASH", "Input file hash does not match FILES.sha256", relative)
        else:
            result["verified"] += 1
    listed = set(entries)
    manifest_rel = manifest.relative_to(raw_root).as_posix()
    actual_files = set()
    for path in raw_root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(raw_root).as_posix()
            if rel != manifest_rel:
                actual_files.add(rel)
    result["unlisted"] = sorted(actual_files - listed)
    if result["unlisted"]:
        audit.critical("MANIFEST-UNLISTED", f"{len(result['unlisted'])} input files are not listed in FILES.sha256")
    return result


def path_under_any(path: Path, roots: Sequence[Path]) -> bool:
    resolved = path.resolve()
    return any(is_relative_to(resolved, root.resolve()) for root in roots if root.exists())


def scan_secrets(raw_root: Path, audit: Audit) -> Dict[str, Any]:
    excluded = [raw_root / "source", raw_root / "generated"]
    findings: List[Dict[str, Any]] = []
    for path in raw_root.rglob("*"):
        if not path.is_file() or path.is_symlink() or path_under_any(path, excluded):
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS and path.name not in {"FILES.sha256"}:
            continue
        try:
            stream = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with stream:
            for line_no, line in enumerate(stream, 1):
                for name, pattern in SECRET_PATTERNS:
                    if pattern.search(line):
                        finding = {"file": path.relative_to(raw_root).as_posix(), "line": line_no, "pattern": name}
                        findings.append(finding)
                        audit.critical("SEC-SECRET", f"Potential secret detected ({name})", f"{finding['file']}:{line_no}")
    return {"finding_count": len(findings), "findings": findings}


def copy_tree_safe(source: Path, destination: Path, predicate=None) -> Tuple[int, int]:
    copied = 0
    skipped = 0
    if not source.exists():
        return copied, skipped
    for current, dirs, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            dirname for dirname in dirs
            if dirname not in SKIP_NAMES and not (current_path / dirname).is_symlink()
        ]
        relative_root = current_path.relative_to(source)
        for filename in files:
            src = current_path / filename
            if src.is_symlink() or not src.is_file():
                skipped += 1
                continue
            if predicate is not None and not predicate(src):
                skipped += 1
                continue
            dst = destination / relative_root / filename
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
    return copied, skipped


def copy_redacted_tree(source: Path, destination: Path) -> int:
    copied = 0
    if not source.exists():
        return copied
    for current, dirs, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        dirs[:] = [d for d in dirs if d not in SKIP_NAMES and not (current_path / d).is_symlink()]
        for filename in files:
            src = current_path / filename
            if src.is_symlink() or not src.is_file():
                continue
            dst = destination / src.relative_to(source)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix.lower() in TEXT_EXTENSIONS or src.name.endswith(".json"):
                with src.open("r", encoding="utf-8", errors="replace") as input_stream, dst.open("w", encoding="utf-8") as output_stream:
                    for line in input_stream:
                        output_stream.write(redact(line))
            else:
                shutil.copy2(src, dst)
            copied += 1
    return copied


def header_candidate(path: Path) -> bool:
    return path.suffix.lower() in HEADER_EXTENSIONS or path.suffix == ""


def parse_reference(reference: str) -> Tuple[str, str]:
    clean = reference.split("#", 1)[0]
    name = clean.split("/", 1)[0]
    version = clean.split("/", 1)[1].split("@", 1)[0] if "/" in clean else ""
    return name, version


def load_conan_packages(raw_root: Path, audit: Audit) -> List[Dict[str, str]]:
    path = raw_root / "conan" / "packages.json"
    if not path.exists():
        audit.critical("CONAN-MANIFEST-MISSING", "conan/packages.json is required for exact package matching")
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        audit.critical("CONAN-MANIFEST-JSON", f"Invalid Conan package manifest: {exc}", str(path.relative_to(raw_root)))
        return []
    packages = data.get("packages") if isinstance(data, dict) else data
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        audit.critical("CONAN-MANIFEST-VERSION", "packages.json schema_version must be 1")
    if not isinstance(packages, list):
        audit.critical("CONAN-MANIFEST-SCHEMA", "packages.json must contain a packages array")
        return []
    result: List[Dict[str, str]] = []
    identities: Set[Tuple[str, str, str, str, str]] = set()
    exported_roots: Set[str] = set()
    for index, item in enumerate(packages):
        if not isinstance(item, dict):
            audit.critical("CONAN-PACKAGE-SCHEMA", "Package entry must be an object", f"packages[{index}]")
            continue
        row = {key: str(item.get(key) or "").strip() for key in (
            "reference", "recipe_revision", "context", "package_id",
            "package_revision", "original_root", "logical_root", "exported_root",
        )}
        if row["context"] not in {"host", "build"}:
            audit.critical("CONAN-PACKAGE-CONTEXT", "Conan context must be host or build", f"packages[{index}]")
        required = ["reference", "context", "package_id"]
        if row["context"] == "host":
            required.extend(["original_root", "exported_root"])
        for key in required:
            if not row[key]:
                audit.critical("CONAN-PACKAGE-REQUIRED", f"Missing Conan package field: {key}", f"packages[{index}]")
        identity = (row["reference"], row["recipe_revision"], row["context"], row["package_id"], row["package_revision"])
        if identity in identities:
            audit.critical("CONAN-PACKAGE-DUPLICATE", "Duplicate exact Conan package identity", f"packages[{index}]")
        identities.add(identity)
        if row["context"] == "host":
            if row["exported_root"] in exported_roots:
                audit.critical("CONAN-EXPORT-REUSED", "One exported_root is assigned to multiple host packages", row["exported_root"])
            exported_roots.add(row["exported_root"])
        result.append(row)
    return result


def extract_graph_identities(raw_root: Path) -> Set[Tuple[str, str, str]]:
    identities: Set[Tuple[str, str, str]] = set()
    for path in sorted((raw_root / "conan").glob("*graph*.json")) if (raw_root / "conan").exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        def walk(value: Any) -> Iterable[Dict[str, Any]]:
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        for item in walk(data):
            reference = str(item.get("ref") or item.get("reference") or "").strip()
            package_id = str(item.get("package_id") or item.get("packageId") or "").strip()
            context = str(item.get("context") or "").strip()
            if reference and package_id:
                identities.add((reference.split("#", 1)[0], context, package_id))
    return identities


def export_conan_packages(
    raw_root: Path,
    package_root: Path,
    packages: List[Dict[str, str]],
    audit: Audit,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    rows: List[Dict[str, str]] = []
    mappings: List[Dict[str, str]] = []
    graph_ids = extract_graph_identities(raw_root)
    seen_destinations: Set[str] = set()
    for package in packages:
        name, version = parse_reference(package["reference"])
        graph_key = (package["reference"].split("#", 1)[0], package["context"], package["package_id"])
        if graph_ids and graph_key not in graph_ids:
            audit.critical("CONAN-GRAPH-MISMATCH", "Package manifest entry is not found in the supplied graph", package["reference"])
        if package["context"] != "host":
            rows.append({**package, "destination": "", "exported_header_count": "0", "status": "IGNORED_BUILD_CONTEXT"})
            continue
        short_id = safe_name(package["package_id"][:16] or "no-package-id")
        destination_name = safe_name(f"{name}-{version}-{short_id}")
        if destination_name in seen_destinations:
            audit.critical("CONAN-DESTINATION-COLLISION", "Two packages resolve to the same destination name", destination_name)
            continue
        seen_destinations.add(destination_name)
        exported = safe_input_path(raw_root, package["exported_root"], audit, "CONAN-EXPORT-PATH")
        destination = package_root / "dependencies" / destination_name
        copied = 0
        if exported is None or not exported.is_dir():
            audit.critical("CONAN-EXPORT-MISSING", "Exported Conan package folder is missing", package["exported_root"])
        else:
            copied, _ = copy_tree_safe(exported, destination, header_candidate)
            if copied == 0:
                audit.critical("CONAN-NO-HEADERS", "No headers copied for host package", package["reference"])
        logical_root = package["logical_root"] or f"conan://{name}/{version}/{package['package_id']}"
        mappings.append({
            "kind": "conan_cache", "old_prefix": package["original_root"],
            "new_prefix": str(destination), "logical_prefix": logical_root,
        })
        if exported is not None:
            mappings.append({
                "kind": "conan_export", "old_prefix": str(exported),
                "new_prefix": str(destination), "logical_prefix": logical_root,
            })
        info = [
            f"Reference={package['reference']}", f"RecipeRevision={package['recipe_revision']}",
            f"Context={package['context']}", f"PackageId={package['package_id']}",
            f"PackageRevision={package['package_revision']}",
            f"OriginalPackageFolder={package['original_root']}",
            f"ExportedFolder={package['exported_root']}", f"LogicalRoot={logical_root}",
            f"CollectedFolder={destination}", f"ExportedHeaderCount={copied}",
        ]
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "CONAN_INFO.txt").write_text("\n".join(info) + "\n", encoding="utf-8")
        rows.append({**package, "destination": str(destination), "exported_header_count": str(copied), "status": "COLLECTED"})
    report = package_root / "cast-config" / "conan-dependencies.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "reference", "recipe_revision", "context", "package_id", "package_revision",
        "original_root", "logical_root", "exported_root", "destination",
        "exported_header_count", "status",
    ]
    with report.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return rows, mappings


def locate_response_file(token: str, directory: str, raw_root: Path) -> Optional[Path]:
    name = token[1:]
    candidates: List[Path] = []
    raw_candidate = raw_root / name
    if raw_candidate.is_file() and not raw_candidate.is_symlink():
        candidates.append(raw_candidate)
    for base in (raw_root / "compilation" / "response-files", raw_root / "build", raw_root):
        if base.exists():
            candidates.extend(path for path in base.rglob(Path(name).name) if path.is_file() and not path.is_symlink())
    unique = sorted(set(path.resolve() for path in candidates))
    return unique[0] if len(unique) == 1 else None


def expand_response_files(
    arguments: List[str], directory: str, raw_root: Path, audit: Audit,
    depth: int = 0, stack: Optional[Set[str]] = None,
) -> Tuple[List[str], List[str]]:
    if depth > 10:
        audit.critical("RESPONSE-DEPTH", "Response-file nesting exceeds 10 levels")
        return arguments, []
    stack = set() if stack is None else set(stack)
    expanded: List[str] = []
    used: List[str] = []
    for argument in arguments:
        if not argument.startswith("@") or len(argument) == 1:
            expanded.append(argument)
            continue
        response = locate_response_file(argument, directory, raw_root)
        if response is None:
            audit.critical("RESPONSE-MISSING", "Response file is missing or ambiguous", argument)
            expanded.append(argument)
            continue
        key = str(response)
        if key in stack:
            audit.critical("RESPONSE-CYCLE", "Response-file inclusion cycle detected", str(response.relative_to(raw_root)))
            continue
        used.append(response.relative_to(raw_root).as_posix())
        if response.stat().st_size > MAX_RESPONSE_FILE_BYTES:
            audit.critical("RESPONSE-SIZE", f"Response file exceeds {MAX_RESPONSE_FILE_BYTES} bytes", str(response.relative_to(raw_root)))
            continue
        try:
            nested_args = shlex.split(response.read_text(encoding="utf-8", errors="replace"), posix=True)
        except ValueError as exc:
            audit.critical("RESPONSE-PARSE", f"Unable to parse response file: {exc}", str(response.relative_to(raw_root)))
            continue
        nested, nested_used = expand_response_files(nested_args, directory, raw_root, audit, depth + 1, stack | {key})
        expanded.extend(nested)
        used.extend(nested_used)
    return expanded, used


def split_compiler_tokens(arguments: List[str]) -> Tuple[str, int]:
    compilers = {"qcc", "q++", "gcc", "g++", "cc", "c++", "clang", "clang++"}
    for index, argument in enumerate(arguments):
        if Path(argument).name in compilers:
            return argument, index
    return arguments[0] if arguments else "", 0


def parse_forwarded_preprocessor(tokens: List[str], result: Dict[str, Any]) -> None:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"-I", "-isystem", "-iquote", "-idirafter", "-D", "-U", "-include", "-imacros"} and index + 1 < len(tokens):
            consume_compiler_option(token, tokens[index + 1], result)
            index += 2
            continue
        consume_joined_option(token, result)
        index += 1


def consume_compiler_option(option: str, value: str, result: Dict[str, Any]) -> None:
    mapping = {
        "-I": ("include_paths", "I"), "-iquote": ("quote_include_paths", "iquote"),
        "-isystem": ("system_include_paths", "isystem"), "-idirafter": ("after_include_paths", "idirafter"),
    }
    if option in mapping:
        key, kind = mapping[option]
        result[key].append(value)
        result["include_search_order"].append({"kind": kind, "path": value, "origin": "command"})
    elif option == "-D":
        result["macros"].append(value)
        result["macro_actions"].append({"action": "define", "value": value})
    elif option == "-U":
        result["undefined_macros"].append(value)
        result["macro_actions"].append({"action": "undef", "value": value})
    elif option == "-include":
        result["force_includes"].append(value)
    elif option == "-imacros":
        result["macro_include_files"].append(value)
    elif option in {"--sysroot", "-isysroot"}:
        result["sysroots"].append(value)


def consume_joined_option(argument: str, result: Dict[str, Any]) -> bool:
    prefixes = [
        ("-isystem=", "-isystem"), ("-isystem", "-isystem"),
        ("-iquote=", "-iquote"), ("-iquote", "-iquote"),
        ("-idirafter=", "-idirafter"), ("-idirafter", "-idirafter"),
        ("--sysroot=", "--sysroot"), ("-isysroot=", "-isysroot"),
        ("-include=", "-include"), ("-imacros=", "-imacros"),
        ("-I", "-I"), ("-D", "-D"), ("-U", "-U"),
    ]
    for prefix, option in prefixes:
        if argument.startswith(prefix) and argument != prefix:
            value = argument[len(prefix):]
            if value.startswith("="):
                value = value[1:]
            consume_compiler_option(option, value, result)
            return True
    return False


def parse_compiler_arguments(arguments: List[str], directory: str, file_name: str) -> Dict[str, Any]:
    compiler, compiler_index = split_compiler_tokens(arguments)
    result: Dict[str, Any] = {
        "directory": directory, "source": file_name, "compiler": compiler,
        "include_paths": [], "quote_include_paths": [], "system_include_paths": [],
        "after_include_paths": [], "include_search_order": [], "macros": [],
        "undefined_macros": [], "macro_actions": [], "force_includes": [],
        "macro_include_files": [], "sysroots": [], "no_standard_includes": [],
        "dependency_options": [], "preprocessor_dump_options": [],
        "preprocessor_passthrough": [], "standards": [], "qcc_variants": [],
        "language": "", "raw_arguments": arguments,
    }
    paired = {"-I", "-iquote", "-isystem", "-idirafter", "-D", "-U", "-include", "-imacros", "--sysroot", "-isysroot"}
    forwarded: List[str] = []
    index = compiler_index + 1
    while index < len(arguments):
        argument = arguments[index]
        if argument in paired and index + 1 < len(arguments):
            consume_compiler_option(argument, arguments[index + 1], result)
            index += 2
            continue
        if consume_joined_option(argument, result):
            index += 1
            continue
        if argument.startswith("-Wp,"):
            payload = [token for token in argument[4:].split(",") if token]
            result["preprocessor_passthrough"].append(argument)
            parse_forwarded_preprocessor(payload, result)
        elif argument == "-Xpreprocessor" and index + 1 < len(arguments):
            forwarded.append(arguments[index + 1])
            result["preprocessor_passthrough"].append(f"-Xpreprocessor {arguments[index + 1]}")
            index += 2
            continue
        elif argument in {"-nostdinc", "-nostdinc++"}:
            result["no_standard_includes"].append(argument)
        elif argument in {"-M", "-MM", "-MD", "-MMD", "-MP", "-MG"}:
            result["dependency_options"].append(argument)
        elif argument in {"-MF", "-MT", "-MQ"} and index + 1 < len(arguments):
            result["dependency_options"].append(f"{argument} {arguments[index + 1]}")
            index += 2
            continue
        elif argument in {"-dM", "-dD", "-dN", "-dI", "-dU"}:
            result["preprocessor_dump_options"].append(argument)
        elif argument.startswith("-std="):
            result["standards"].append(argument.split("=", 1)[1])
        elif argument == "-x" and index + 1 < len(arguments):
            result["language"] = arguments[index + 1]
            index += 2
            continue
        elif argument.startswith("-V") and len(argument) > 2:
            result["qcc_variants"].append(argument[2:])
        index += 1
    if forwarded:
        parse_forwarded_preprocessor(forwarded, result)
    if not result["language"]:
        suffix = Path(file_name).suffix.lower()
        result["language"] = "c++" if suffix in {".cc", ".cpp", ".cxx", ".c++"} else "c"
    return result


def load_compilation_units(raw_root: Path, audit: Audit) -> List[Dict[str, Any]]:
    normalized_path = raw_root / "compilation" / "compilation-units.json"
    entries: List[Any] = []
    if normalized_path.exists():
        try:
            data = json.loads(normalized_path.read_text(encoding="utf-8"))
            entries = data.get("units", []) if isinstance(data, dict) else data
        except (OSError, json.JSONDecodeError) as exc:
            audit.critical("COMPILATION-UNITS-JSON", f"Invalid compilation-units.json: {exc}")
    else:
        databases = sorted(raw_root.rglob("compile_commands.json"))
        if not databases:
            audit.critical("COMPILATION-DATABASE-MISSING", "compile_commands.json or compilation/compilation-units.json is required")
            return []
        for path in databases:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                audit.critical("COMPILATION-DATABASE-JSON", f"Invalid compilation database: {exc}", str(path.relative_to(raw_root)))
                continue
            if isinstance(data, list):
                entries.extend(data)
            else:
                audit.critical("COMPILATION-DATABASE-SCHEMA", "Compilation database root must be an array", str(path.relative_to(raw_root)))
    records: List[Dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            audit.critical("COMPILATION-ENTRY-SCHEMA", "Compilation entry must be an object", f"entry[{index}]")
            continue
        directory = str(entry.get("directory") or "")
        source = str(entry.get("file") or entry.get("source") or "")
        arguments = entry.get("expanded_arguments") or entry.get("arguments")
        if not isinstance(arguments, list):
            command = str(entry.get("command") or "")
            try:
                arguments = shlex.split(command, posix=True)
            except ValueError as exc:
                audit.critical("COMPILATION-COMMAND-PARSE", f"Unable to parse compiler command: {exc}", source)
                continue
        string_args = [str(item) for item in arguments]
        expanded, response_files = expand_response_files(string_args, directory, raw_root, audit)
        record = parse_compiler_arguments(expanded, directory, source)
        record["response_files"] = response_files
        records.append(record)
    if not records:
        audit.critical("COMPILATION-EMPTY", "No valid compilation units were found")
    return records


def load_qcc_probes(raw_root: Path, records: List[Dict[str, Any]], metadata: Dict[str, str], audit: Audit) -> Dict[Tuple[str, str], Dict[str, Any]]:
    manifest = raw_root / "compiler" / "qcc-variants.json"
    probes: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                audit.critical("QCC-PROBE-VERSION", "qcc-variants.json schema_version must be 1")
            variants = data.get("variants", []) if isinstance(data, dict) else data
        except (OSError, json.JSONDecodeError) as exc:
            audit.critical("QCC-PROBE-JSON", f"Invalid qcc-variants.json: {exc}")
            variants = []
        if not isinstance(variants, list):
            audit.critical("QCC-PROBE-SCHEMA", "qcc-variants.json must contain a variants array")
            variants = []
        for item in variants:
            if not isinstance(item, dict):
                continue
            variant = str(item.get("variant") or "")
            language = str(item.get("language") or "c")
            macros_rel = str(item.get("macros_file") or "")
            includes_rel = str(item.get("include_search_file") or "")
            key = (variant, language)
            if not variant or language not in {"c", "c++"}:
                audit.critical("QCC-PROBE-SCHEMA", "Each QCC probe needs a variant and language c or c++")
            if key in probes:
                audit.critical("QCC-PROBE-DUPLICATE", "Duplicate QCC probe variant/language", f"{variant}/{language}")
            probe: Dict[str, Any] = {
                "variant": variant, "language": language, "macros_file": macros_rel,
                "include_search_file": includes_rel, "implicit_includes": [],
                "implicit_macro_actions": [],
            }
            for field, relative in (("macros_file", macros_rel), ("include_search_file", includes_rel)):
                path = safe_input_path(raw_root, relative, audit, "QCC-PROBE-PATH") if relative else None
                if path is None or not path.is_file():
                    audit.critical("QCC-PROBE-MISSING", f"Missing QCC probe file: {field}", relative)
                    continue
                if path.stat().st_size > MAX_PROBE_FILE_BYTES:
                    audit.critical("QCC-PROBE-SIZE", f"QCC probe exceeds {MAX_PROBE_FILE_BYTES} bytes", relative)
                    continue
                if field == "include_search_file":
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                    inside = False
                    for line in lines:
                        stripped = line.strip()
                        if "search starts here" in stripped:
                            inside = True
                            continue
                        if inside and "End of search list" in stripped:
                            inside = False
                            continue
                        if inside and stripped and not stripped.startswith("("):
                            probe["implicit_includes"].append(stripped)
                else:
                    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                        define = re.match(r"^\s*#\s*define\s+([A-Za-z_]\w*(?:\([^)]*\))?)(?:\s+(.*))?$", line)
                        undef = re.match(r"^\s*#\s*undef\s+([A-Za-z_]\w*)", line)
                        if define:
                            value = define.group(1)
                            if define.group(2):
                                value += "=" + define.group(2).strip()
                            probe["implicit_macro_actions"].append({"action": "define", "value": value, "origin": "qcc-probe"})
                        elif undef:
                            probe["implicit_macro_actions"].append({"action": "undef", "value": undef.group(1), "origin": "qcc-probe"})
            probes[key] = probe
    required: Set[Tuple[str, str]] = set()
    fallback_variant = metadata.get("CompilerVariant", "")
    for record in records:
        if Path(record["compiler"]).name not in {"qcc", "q++"}:
            continue
        variants = record["qcc_variants"] or ([fallback_variant] if fallback_variant else [])
        if not variants:
            audit.critical("QCC-VARIANT-MISSING", "QCC compilation unit has no -V variant and identity has no CompilerVariant", record["source"])
            continue
        for variant in variants:
            required.add((variant, record["language"]))
            probe = probes.get((variant, record["language"])) or probes.get((variant, "c"))
            if probe:
                for include in probe["implicit_includes"]:
                    record["include_search_order"].append({"kind": "implicit-system", "path": include, "origin": "qcc-probe"})
                record["macro_actions"] = probe["implicit_macro_actions"] + record["macro_actions"]
            else:
                audit.critical("QCC-PROBE-NOT-FOUND", "No QCC macro/include probe for compiler variant", f"{variant}/{record['language']}")
    return probes


def normalize_path(value: str, directory: str, sysroot: str = "") -> str:
    cleaned = value.strip().strip('"').strip("'")
    if cleaned.startswith("$SYSROOT"):
        cleaned = (sysroot.rstrip("/") + cleaned[len("$SYSROOT"):]) if sysroot else cleaned
    elif cleaned.startswith("="):
        cleaned = (sysroot.rstrip("/") + "/" + cleaned[1:].lstrip("/")) if sysroot else cleaned
    if "://" in cleaned:
        return cleaned
    if os.path.isabs(cleaned):
        return os.path.normpath(cleaned)
    return os.path.normpath(os.path.join(directory or ".", cleaned))


def remap_path(value: str, directory: str, sysroot: str, mappings: List[Dict[str, str]]) -> Dict[str, str]:
    original = normalize_path(value, directory, sysroot)
    for mapping in sorted(mappings, key=lambda item: len(os.path.normpath(item["old_prefix"])), reverse=True):
        old = os.path.normpath(mapping["old_prefix"])
        if not old:
            continue
        if "://" in original:
            logical = mapping["logical_prefix"].rstrip("/")
            if original == logical or original.startswith(logical + "/"):
                relative = original[len(logical):].lstrip("/")
            else:
                continue
        else:
            try:
                if os.path.commonpath([old, original]) != old:
                    continue
            except ValueError:
                continue
            relative = os.path.relpath(original, old)
            if relative == ".":
                relative = ""
        physical = Path(mapping["new_prefix"]) / relative
        logical_path = mapping["logical_prefix"].rstrip("/") + (("/" + relative.replace(os.sep, "/")) if relative else "")
        return {
            "original": value, "resolved_original": original, "physical": str(physical),
            "logical": logical_path, "status": "mapped" if physical.exists() else "mapped_target_missing",
            "rule": mapping["kind"],
        }
    return {"original": value, "resolved_original": original, "physical": "", "logical": "", "status": "unresolved", "rule": "no_mapping"}


def define_line(value: str) -> str:
    if "=" in value:
        name, definition = value.split("=", 1)
        return f"#define {name} {definition}"
    return f"#define {value} 1"


def macro_name(value: str) -> str:
    head = value.split("=", 1)[0]
    return head.split("(", 1)[0]


def profile_force_header(profile: Dict[str, Any], package_root: Path, destination: Path) -> None:
    lines = ["/* Generated by CAST offline collector; review before use. */"]
    for action in profile["macro_actions"]:
        if action["action"] == "define":
            lines.append(f"#undef {macro_name(action['value'])}")
            lines.append(define_line(action["value"]))
        else:
            lines.append(f"#undef {action['value']}")
    for item in profile["mapped_macro_files"] + profile["mapped_force_includes"]:
        if item["status"] != "mapped":
            continue
        relative = os.path.relpath(item["physical"], destination.parent).replace(os.sep, "/")
        lines.append(f'#include "{relative}"')
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def map_records_and_build_profiles(
    records: List[Dict[str, Any]], mappings: List[Dict[str, str]], probes: Dict[Tuple[str, str], Dict[str, Any]],
    package_root: Path, audit: Audit,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    remap_rows: List[Dict[str, Any]] = []
    profiles_by_signature: Dict[str, Dict[str, Any]] = {}
    source_rows: List[Dict[str, Any]] = []
    effective_kinds = ["iquote", "I", "isystem", "implicit-system", "idirafter"]
    for command_index, record in enumerate(records):
        sysroot = record["sysroots"][0] if record["sysroots"] else ""
        source_mapping = remap_path(record["source"], record["directory"], sysroot, mappings)
        source_rows.append({"source": record["source"], **source_mapping})
        if source_mapping["status"] != "mapped" or not Path(source_mapping["physical"]).is_file():
            audit.critical("SOURCE-NOT-COLLECTED", "Compiled source cannot be mapped to a collected file", record["source"])
        ordered = []
        for kind in effective_kinds:
            ordered.extend(item for item in record["include_search_order"] if item["kind"] == kind)
        mapped_includes = []
        for position, item in enumerate(ordered):
            mapped = remap_path(item["path"], record["directory"], sysroot, mappings)
            mapped.update({"kind": item["kind"], "origin": item.get("origin", "command")})
            mapped_includes.append(mapped)
            remap_rows.append({
                "command_index": command_index, "source": record["source"], "category": "include",
                "position": position, "kind": item["kind"], **mapped,
            })
            if mapped["status"] != "mapped":
                audit.critical("INCLUDE-UNRESOLVED", "Compiler include path could not be mapped", f"{record['source']} | {item['path']}")
        mapped_force = []
        for position, value in enumerate(record["force_includes"]):
            mapped = remap_path(value, record["directory"], sysroot, mappings)
            mapped_force.append(mapped)
            remap_rows.append({
                "command_index": command_index, "source": record["source"], "category": "force-include",
                "position": position, "kind": "include", **mapped,
            })
            if mapped["status"] != "mapped" or not Path(mapped["physical"]).is_file():
                audit.critical("FORCE-INCLUDE-UNRESOLVED", "Force include file could not be mapped", f"{record['source']} | {value}")
        mapped_macro_files = []
        for position, value in enumerate(record["macro_include_files"]):
            mapped = remap_path(value, record["directory"], sysroot, mappings)
            mapped_macro_files.append(mapped)
            remap_rows.append({
                "command_index": command_index, "source": record["source"], "category": "imacros",
                "position": position, "kind": "imacros", **mapped,
            })
            if mapped["status"] != "mapped" or not Path(mapped["physical"]).is_file():
                audit.critical("IMACROS-UNRESOLVED", "-imacros file could not be mapped", f"{record['source']} | {value}")
        profile_basis = {
            "language": record["language"], "standards": record["standards"],
            "compiler": Path(record["compiler"]).name, "qcc_variants": record["qcc_variants"],
            "includes": [(item["kind"], item["logical"], item["status"]) for item in mapped_includes],
            "macro_actions": record["macro_actions"],
            "force_includes": [item["logical"] for item in mapped_force],
            "macro_include_files": [item["logical"] for item in mapped_macro_files],
            "sysroots": record["sysroots"], "no_standard_includes": record["no_standard_includes"],
        }
        signature = hashlib.sha256(json.dumps(profile_basis, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        profile_id = f"profile-{signature}"
        profile = profiles_by_signature.setdefault(profile_id, {
            "profile_id": profile_id, **profile_basis, "sources": [],
            "mapped_includes": mapped_includes, "mapped_force_includes": mapped_force,
            "mapped_macro_files": mapped_macro_files,
        })
        profile["sources"].append(source_mapping["logical"] or record["source"])
        record["mapped_source"] = source_mapping
        record["mapped_includes"] = mapped_includes
        record["mapped_force_includes"] = mapped_force
        record["mapped_macro_files"] = mapped_macro_files
        record["profile_id"] = profile_id
    profiles = sorted(profiles_by_signature.values(), key=lambda item: item["profile_id"])
    config_dir = package_root / "cast-config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for profile in profiles:
        header = config_dir / f"force-include-{profile['profile_id']}.h"
        profile_force_header(profile, package_root, header)
        profile["generated_force_include"] = str(header.relative_to(package_root))
    with (config_dir / "path-remapping.csv").open("w", encoding="utf-8", newline="") as stream:
        fieldnames = [
            "command_index", "source", "category", "position", "kind", "original",
            "resolved_original", "physical", "logical", "status", "rule", "origin",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(remap_rows)
    return records, profiles, source_rows


def package_relative(path: str, package_root: Path) -> str:
    try:
        return Path(path).relative_to(package_root).as_posix()
    except ValueError:
        return path


def write_compilation_outputs(records: List[Dict[str, Any]], profiles: List[Dict[str, Any]], source_rows: List[Dict[str, Any]], package_root: Path) -> None:
    config = package_root / "cast-config"
    write_json(config / "compiler-invocations.json", records)
    write_json(config / "compilation-profiles.json", {"profiles": profiles})
    analysis_units = []
    for profile in profiles:
        source_groups = {
            "MANUAL": [source for source in profile["sources"] if not source.startswith("source://generated")],
            "GENERATED": [source for source in profile["sources"] if source.startswith("source://generated")],
        }
        for category, sources in source_groups.items():
            if not sources:
                continue
            relative_includes = [
                package_relative(item["physical"], package_root)
                for item in profile["mapped_includes"] if item["status"] == "mapped"
            ]
            analysis_units.append({
                "analysis_unit": category + "-" + profile["profile_id"],
                "source_category": category.lower(),
                "profile_id": profile["profile_id"], "sources": sources,
                "includes": relative_includes,
                "path_base": "PACKAGE_ROOT",
                "macros": [item["value"] for item in profile["macro_actions"] if item["action"] == "define"],
                "undefined_macros": [item["value"] for item in profile["macro_actions"] if item["action"] == "undef"],
                "force_include": profile["generated_force_include"],
            })
    write_json(config / "analysis-units.json", {"analysis_units": analysis_units})
    with (config / "source-coverage.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source", "original", "resolved_original", "physical", "logical", "status", "rule"])
        writer.writeheader()
        for row in source_rows:
            writer.writerow(row)
    include_paths = []
    seen = set()
    for profile in profiles:
        for item in profile["mapped_includes"]:
            if item["status"] == "mapped" and item["physical"] not in seen:
                seen.add(item["physical"])
                include_paths.append(item["physical"])
    (config / "cast-include-paths.absolute.txt").write_text("\n".join(include_paths) + ("\n" if include_paths else ""), encoding="utf-8")
    relative_paths = []
    for path in include_paths:
        try:
            relative_paths.append(Path(path).relative_to(package_root).as_posix())
        except ValueError:
            relative_paths.append(path)
    (config / "cast-include-paths.relative.txt").write_text("\n".join(relative_paths) + ("\n" if relative_paths else ""), encoding="utf-8")
    unresolved = [row for row in source_rows if row["status"] != "mapped"]
    for record in records:
        for category, items in (
            ("include", record["mapped_includes"]),
            ("force-include", record["mapped_force_includes"]),
            ("imacros", record["mapped_macro_files"]),
        ):
            for item in items:
                if item["status"] != "mapped":
                    unresolved.append({
                        "source": record["source"], "original": item["original"],
                        "status": f"{category}:{item['status']}",
                    })
    (config / "unresolved-paths.txt").write_text("\n".join(f"{row.get('source','')} | {row.get('original','')} | {row.get('status','')}" for row in unresolved) + ("\n" if unresolved else ""), encoding="utf-8")
    lines = ["# CAST analysis plan", "", "Generated from exact compilation profiles. Review before applying in CAST Imaging.", ""]
    for unit in analysis_units:
        lines.extend([
            f"## {unit['analysis_unit']}", "", f"Profile: `{unit['profile_id']}`", "",
            "Sources:", *[f"- `{source}`" for source in unit["sources"]], "",
            "Includes (ordered):", *[f"- `{path}`" for path in unit["includes"]], "",
            "Macros:", *[f"- `{value}`" for value in unit["macros"]], "",
            "Undefined macros:", *[f"- `{value}`" for value in unit["undefined_macros"]], "",
            f"Force include: `{unit['force_include']}`", "",
        ])
    (config / "CAST_ANALYSIS_PLAN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_dependency_rules(path: Path) -> List[Tuple[str, List[str]]]:
    text = path.read_text(encoding="utf-8", errors="replace").replace("\\\r\n", " ").replace("\\\n", " ")
    rules: List[Tuple[str, List[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        target, dependencies = line.split(":", 1)
        if not dependencies.strip():
            continue
        lexer = shlex.shlex(dependencies, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            values = list(lexer)
        except ValueError:
            values = dependencies.split()
        rules.append((target.strip(), values))
    return rules


def collect_dependency_files(raw_root: Path, package_root: Path) -> Dict[str, int]:
    rows: List[Dict[str, str]] = []
    count = 0
    destination = package_root / "evidence" / "dependency-files"
    for path in sorted(raw_root.rglob("*.d")):
        if path.is_symlink() or not path.is_file():
            continue
        count += 1
        dst = destination / f"{count:06d}_{path.name}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        for target, dependencies in parse_dependency_rules(path):
            for dependency in dependencies:
                rows.append({"dependency_file": path.relative_to(raw_root).as_posix(), "target": target, "dependency": dependency})
    report = package_root / "cast-config" / "dependency-files.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["dependency_file", "target", "dependency"])
        writer.writeheader()
        writer.writerows(rows)
    return {"dependency_file_count": count, "dependency_edge_count": len(rows)}


def assess_compiled_source_coverage(package_root: Path, source_rows: List[Dict[str, Any]], audit: Audit) -> Dict[str, Any]:
    compiled = {
        str(Path(row["physical"]).resolve()) for row in source_rows
        if row["status"] == "mapped" and row.get("physical")
    }
    candidates = sorted(
        path for path in (package_root / "source").rglob("*")
        if path.is_file() and path.suffix.lower() in {".c", ".cc", ".cpp", ".cxx", ".c++"}
    )
    uncovered = [path.relative_to(package_root).as_posix() for path in candidates if str(path.resolve()) not in compiled]
    if uncovered:
        audit.critical(
            "SOURCE-COVERAGE-INCOMPLETE",
            f"{len(uncovered)} collected C/C++ source files have no compilation unit",
            uncovered[0],
        )
    result = {
        "eligible_source_count": len(candidates),
        "compiled_source_count": len(candidates) - len(uncovered),
        "coverage_percent": round(100.0 * (len(candidates) - len(uncovered)) / len(candidates), 2) if candidates else 100.0,
        "uncovered_sources": uncovered,
    }
    write_json(package_root / "cast-config" / "source-coverage-summary.json", result)
    return result


def compute_volumetry(package_root: Path) -> Dict[str, Any]:
    components = {
        "manual": package_root / "source" / "manual",
        "generated": package_root / "source" / "generated",
        "dependencies": package_root / "dependencies",
        "qnx": package_root / "platform" / "qnx",
        "evidence": package_root / "evidence",
    }
    result: Dict[str, Any] = {"components": {}}
    for name, root in components.items():
        files = [path for path in root.rglob("*") if path.is_file()] if root.exists() else []
        lines = 0
        for path in files:
            if path.suffix.lower() in SOURCE_EXTENSIONS | HEADER_EXTENSIONS:
                try:
                    with path.open("r", encoding="utf-8", errors="replace") as stream:
                        lines += sum(1 for _ in stream)
                except OSError:
                    pass
        result["components"][name] = {
            "file_count": len(files), "bytes": sum(path.stat().st_size for path in files),
            "source_and_header_lines": lines,
        }
    application_lines = result["components"]["manual"]["source_and_header_lines"] + result["components"]["generated"]["source_and_header_lines"]
    generated_lines = result["components"]["generated"]["source_and_header_lines"]
    result["generated_line_percent"] = round(100.0 * generated_lines / application_lines, 2) if application_lines else 0.0
    write_json(package_root / "volumetry.json", result)
    return result


def write_identity(metadata: Dict[str, str], structured: Dict[str, Any], package_root: Path) -> None:
    identity = dict(structured) if structured else {"legacy_properties": metadata}
    identity["collector"] = {"name": "CAST offline collector", "version": COLLECTOR_VERSION, "timestamp_utc": utc_iso()}
    write_json(package_root / "identity" / "BUILD_IDENTITY.json", identity)
    values = dict(metadata)
    values["CollectorVersion"] = COLLECTOR_VERSION
    values["CollectionTimestampUTC"] = utc_iso()
    (package_root / "identity" / "BUILD_IDENTITY.txt").write_text("\n".join(f"{key}={values[key]}" for key in sorted(values)) + "\n", encoding="utf-8")


def compare_baseline(package_root: Path, baseline: Optional[Path]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"baseline": "", "changes": []}
    if baseline is None:
        return result
    baseline = baseline.resolve()
    result["baseline"] = str(baseline)
    def canonical_identity(path: Path) -> Any:
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("collector"), dict):
            value["collector"].pop("timestamp_utc", None)
        return value

    def canonical_conan(path: Path) -> Any:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        stable = [
            {key: row.get(key, "") for key in (
                "reference", "recipe_revision", "context", "package_id",
                "package_revision", "logical_root", "exported_header_count", "status",
            )}
            for row in rows
        ]
        return sorted(stable, key=lambda row: tuple(row.values()))

    def canonical_profiles(path: Path, root: Path) -> Any:
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))

        def normalize(item: Any) -> Any:
            if isinstance(item, dict):
                return {key: normalize(child) for key, child in item.items() if key != "physical"}
            if isinstance(item, list):
                return [normalize(child) for child in item]
            if isinstance(item, str):
                prefix = str(root) + os.sep
                return "$PACKAGE_ROOT/" + item[len(prefix):].replace(os.sep, "/") if item.startswith(prefix) else item
            return item

        return normalize(value)

    pairs = [
        ("identity", canonical_identity(baseline / "identity" / "BUILD_IDENTITY.json"), canonical_identity(package_root / "identity" / "BUILD_IDENTITY.json")),
        ("conan", canonical_conan(baseline / "cast-config" / "conan-dependencies.csv"), canonical_conan(package_root / "cast-config" / "conan-dependencies.csv")),
        ("profiles", canonical_profiles(baseline / "cast-config" / "compilation-profiles.json", baseline), canonical_profiles(package_root / "cast-config" / "compilation-profiles.json", package_root)),
    ]
    for name, old_value, new_value in pairs:
        old_hash = hashlib.sha256(json.dumps(old_value, sort_keys=True).encode("utf-8")).hexdigest() if old_value is not None else ""
        new_hash = hashlib.sha256(json.dumps(new_value, sort_keys=True).encode("utf-8")).hexdigest() if new_value is not None else ""
        if old_hash != new_hash:
            result["changes"].append({"component": name, "baseline_sha256": old_hash, "current_sha256": new_hash})
    write_json(package_root / "cast-config" / "baseline-diff.json", result)
    return result


def analyze_cast_logs(raw_root: Path, package_root: Path, audit: Audit) -> Dict[str, Any]:
    log_root = raw_root / "cast-analysis-logs"
    counts = {key: 0 for key in ANALYSIS_PATTERNS}
    files = []
    if log_root.exists():
        for path in sorted(log_root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            files.append(path.relative_to(raw_root).as_posix())
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    for key, pattern in ANALYSIS_PATTERNS.items():
                        counts[key] += len(pattern.findall(line))
    total = sum(counts.values())
    status = "NOT_RUN" if not files else ("NOT_QUALIFIED" if total else "QUALIFIED_WITH_RESERVATIONS")
    if files and total:
        audit.critical("CAST-ANALYSIS-LOG-ERRORS", f"CAST logs contain {total} qualifying error indicators")
    result = {"status": status, "log_files": files, "counts": counts, "manual_review_required": bool(files)}
    write_json(package_root / "qualification.json", result)
    return result


def write_output_manifest(root: Path) -> None:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "manifest.sha256"):
        rows.append(f"{sha256(path)}  {path.relative_to(root).as_posix()}")
    (root / "manifest.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")


def write_audit_outputs(package_root: Path, audit: Audit, summary: Dict[str, Any]) -> None:
    status = audit.status()
    report = {
        "collector_version": COLLECTOR_VERSION, "mode": audit.mode, "status": status,
        "critical_count": audit.critical_count, "warning_count": audit.warning_count,
        "issues": audit.issues, "summary": summary,
    }
    write_json(package_root / "collection-report.json", report)
    write_json(package_root / "collection-status.json", {"status": status, "mode": audit.mode, "critical_count": audit.critical_count, "warning_count": audit.warning_count})
    lines = [f"[{item['severity']}] {item['code']}: {item['message']}" + (f" ({item['path']})" if item["path"] else "") for item in audit.issues]
    (package_root / "WARNINGS.txt").write_text("\n".join(lines) + ("\n" if lines else "No issue.\n"), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a hardened CAST package without executing build tools.")
    parser.add_argument("--input-root", required=True, type=Path, help="Client-provided CAST_EVIDENCE_BUNDLE")
    parser.add_argument("--output-parent", required=True, type=Path, help="Parent directory for generated packages")
    parser.add_argument("--application", help="Override application name")
    parser.add_argument("--target", help="Override target label")
    parser.add_argument("--mode", choices=("strict", "exploratory"), default="strict")
    parser.add_argument("--baseline", type=Path, help="Previous collected package for drift comparison")
    parser.add_argument("--max-files", type=int, default=500000)
    parser.add_argument("--max-bytes", type=int, default=20 * 1024 * 1024 * 1024)
    parser.add_argument("--no-zip", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    raw_root = args.input_root.resolve()
    output_parent = args.output_parent.resolve()
    if not raw_root.is_dir():
        raise SystemExit(f"Input root does not exist: {raw_root}")
    if is_relative_to(output_parent, raw_root):
        raise SystemExit("Output parent must not be located inside CLIENT_DROP")
    output_parent.mkdir(parents=True, exist_ok=True)
    audit = Audit(args.mode)
    input_stats = scan_input_tree(raw_root, audit, args.max_files, args.max_bytes)
    metadata, structured_identity = load_identity(raw_root, audit)
    application = args.application or metadata.get("Application") or "UNKNOWN-APPLICATION"
    target = args.target or metadata.get("TargetLabel") or "UNKNOWN-TARGET"
    build_id = safe_name(f"{application}-{target}-{metadata.get('GitCommit','unknown')[:12]}")
    package_root = output_parent / f"{build_id}-CAST-{utc_stamp()}"
    package_root.mkdir(parents=True)

    summary: Dict[str, Any] = {
        "application": application, "target": target, "build_id": build_id,
        "input_statistics": input_stats,
    }
    write_identity(metadata, structured_identity, package_root)
    manifest_result = verify_input_manifest(raw_root, audit)
    write_json(package_root / "evidence" / "input-manifest-verification.json", manifest_result)
    if (raw_root / "FILES.sha256").is_file():
        shutil.copy2(raw_root / "FILES.sha256", package_root / "evidence" / "CLIENT_FILES.sha256")
    elif (raw_root / "identity" / "FILES.sha256").is_file():
        shutil.copy2(raw_root / "identity" / "FILES.sha256", package_root / "evidence" / "CLIENT_FILES.sha256")
    security_result = scan_secrets(raw_root, audit)
    write_json(package_root / "security" / "sanitization-report.json", security_result)

    mappings: List[Dict[str, str]] = []
    source_root = raw_root / "source"
    generated_root = raw_root / "generated"
    manual_destination = package_root / "source" / "manual"
    generated_destination = package_root / "source" / "generated"
    if not source_root.is_dir():
        audit.critical("SOURCE-MISSING", "source/ directory is required")
    else:
        copied, skipped = copy_tree_safe(source_root, manual_destination)
        summary["manual_source_files_copied"] = copied
        summary["manual_source_files_skipped"] = skipped
        mappings.append({"kind": "client_drop_source", "old_prefix": str(source_root), "new_prefix": str(manual_destination), "logical_prefix": "source://manual"})
        for key in ("SourceRoot", "RepositoryRoot", "BuildSourceRoot"):
            if metadata.get(key):
                mappings.append({"kind": key, "old_prefix": metadata[key], "new_prefix": str(manual_destination), "logical_prefix": "source://manual"})
    if generated_root.is_dir():
        copied, skipped = copy_tree_safe(generated_root, generated_destination)
        summary["generated_files_copied"] = copied
        summary["generated_files_skipped"] = skipped
        mappings.append({"kind": "client_drop_generated", "old_prefix": str(generated_root), "new_prefix": str(generated_destination), "logical_prefix": "source://generated"})
        if metadata.get("GeneratedRoot"):
            mappings.append({"kind": "GeneratedRoot", "old_prefix": metadata["GeneratedRoot"], "new_prefix": str(generated_destination), "logical_prefix": "source://generated"})

    qnx_root = raw_root / "qnx"
    qnx_destination = package_root / "platform" / "qnx"
    if qnx_root.is_dir():
        copied, skipped = copy_tree_safe(qnx_root, qnx_destination, header_candidate)
        summary["qnx_files_copied"] = copied
        summary["qnx_files_skipped"] = skipped
        mappings.append({"kind": "client_drop_qnx", "old_prefix": str(qnx_root), "new_prefix": str(qnx_destination), "logical_prefix": f"qnx://{metadata.get('TargetOSVersion','unknown')}"})
        if metadata.get("QNX_TARGET"):
            mappings.append({"kind": "QNX_TARGET", "old_prefix": metadata["QNX_TARGET"], "new_prefix": str(qnx_destination), "logical_prefix": f"qnx://{metadata.get('TargetOSVersion','unknown')}"})
    elif metadata.get("TargetOS", "").lower() == "qnx":
        audit.critical("QNX-HEADERS-MISSING", "QNX target selected but qnx/ header mirror is missing")

    packages = load_conan_packages(raw_root, audit)
    conan_rows, conan_mappings = export_conan_packages(raw_root, package_root, packages, audit)
    mappings.extend(conan_mappings)
    summary["conan_packages"] = len(conan_rows)

    records = load_compilation_units(raw_root, audit)
    probes = load_qcc_probes(raw_root, records, metadata, audit)
    records, profiles, source_rows = map_records_and_build_profiles(records, mappings, probes, package_root, audit)
    write_compilation_outputs(records, profiles, source_rows, package_root)
    summary["compilation_units"] = len(records)
    summary["compilation_profiles"] = len(profiles)
    summary["compiled_sources_mapped"] = sum(1 for row in source_rows if row["status"] == "mapped" and Path(row["physical"]).is_file())
    summary["source_coverage"] = assess_compiled_source_coverage(package_root, source_rows, audit)

    dependency_stats = collect_dependency_files(raw_root, package_root)
    summary.update(dependency_stats)
    if dependency_stats["dependency_file_count"] == 0:
        audit.warning("DEPENDENCY-FILES-MISSING", "No .d dependency files supplied")

    copy_redacted_tree(raw_root / "logs", package_root / "evidence" / "logs")
    copy_redacted_tree(raw_root / "evidence", package_root / "evidence" / "client")
    copy_redacted_tree(raw_root / "compiler", package_root / "evidence" / "compiler")
    copy_redacted_tree(raw_root / "compilation", package_root / "evidence" / "compilation")
    for source, destination in (
        (raw_root / "conan" / "profiles", package_root / "evidence" / "conan" / "profiles"),
        (raw_root / "conan" / "lockfiles", package_root / "evidence" / "conan" / "lockfiles"),
        (raw_root / "conan" / "generated", package_root / "evidence" / "conan" / "generated"),
    ):
        copy_redacted_tree(source, destination)

    baseline_result = compare_baseline(package_root, args.baseline)
    summary["baseline_changes"] = len(baseline_result.get("changes", []))
    qualification = analyze_cast_logs(raw_root, package_root, audit)
    summary["analysis_log_status"] = qualification["status"]
    summary["volumetry"] = compute_volumetry(package_root)

    write_audit_outputs(package_root, audit, summary)
    write_output_manifest(package_root)
    archive = ""
    if not args.no_zip and not (args.mode == "strict" and audit.critical_count):
        archive = shutil.make_archive(str(package_root), "zip", root_dir=package_root.parent, base_dir=package_root.name)
    result = {
        "package_directory": str(package_root), "archive": archive,
        "status": audit.status(), "critical_count": audit.critical_count,
        "warning_count": audit.warning_count,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 2 if args.mode == "strict" and audit.critical_count else 0


def cli() -> int:
    try:
        return main()
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "OPERATIONAL_ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(cli())
