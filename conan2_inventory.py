#!/usr/bin/env python3
"""Create the exact Conan 2 package inventory inside the client CI environment."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def nodes_from_graph(data):
    graph = data.get("graph", data) if isinstance(data, dict) else {}
    nodes = graph.get("nodes", {}) if isinstance(graph, dict) else {}
    if isinstance(nodes, dict):
        return [value for value in nodes.values() if isinstance(value, dict)]
    if isinstance(nodes, list):
        return [value for value in nodes if isinstance(value, dict)]
    return []


def split_reference(reference):
    base, separator, revision = reference.partition("#")
    return base, revision if separator else ""


def cache_path(conan_command, package_reference):
    process = subprocess.run(
        [conan_command, "cache", "path", package_reference],
        check=False, capture_output=True, text=True,
    )
    if process.returncode:
        raise RuntimeError(f"conan cache path failed for {package_reference}: {process.stderr.strip()}")
    lines = [line.strip() for line in process.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError(f"Unexpected conan cache path output for {package_reference}")
    path = Path(lines[0]).resolve()
    if not path.is_dir():
        raise RuntimeError(f"Conan package directory is unavailable: {path}")
    return str(path)


def make_inventory(graph_data, conan_command="conan"):
    packages = []
    identities = set()
    for node in nodes_from_graph(graph_data):
        reference = str(node.get("ref") or node.get("reference") or "").strip()
        package_id = str(node.get("package_id") or node.get("packageId") or "").strip()
        if not reference or not package_id:
            continue
        context = str(node.get("context") or "host").strip().lower()
        package_revision = str(node.get("prev") or node.get("package_revision") or node.get("packageRevision") or "").strip()
        base_reference, recipe_revision = split_reference(reference)
        identity = (base_reference, recipe_revision, context, package_id, package_revision)
        if identity in identities:
            continue
        identities.add(identity)
        exact_reference = base_reference + (("#" + recipe_revision) if recipe_revision else "") + ":" + package_id
        if package_revision:
            exact_reference += "#" + package_revision
        original_root = cache_path(conan_command, exact_reference)
        packages.append({
            "reference": base_reference,
            "recipe_revision": recipe_revision,
            "context": context,
            "package_id": package_id,
            "package_revision": package_revision,
            "original_root": original_root,
            "logical_root": f"conan://{base_reference}/{package_id}",
            "exported_root": "",
        })
    if not packages:
        raise RuntimeError("No binary package node found in the Conan graph")
    return {"schema_version": 1, "packages": packages}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Resolve exact Conan 2 package folders from a graph JSON.")
    parser.add_argument("--graph", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--conan-command", default="conan")
    args = parser.parse_args(argv)
    graph_data = json.loads(args.graph.read_text(encoding="utf-8"))
    inventory = make_inventory(graph_data, args.conan_command)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "packages": len(inventory["packages"])}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
