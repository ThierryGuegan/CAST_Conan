import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cast_offline_collector as collector
import client_ci_export
import conan2_inventory


def put(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(content, encoding="utf-8")



def valid_fixture(root, two_profiles=False):
    identity = {
        "schema_version": 1,
        "application": "AppA",
        "application_version": "1.2.3",
        "git_commit": "0123456789abcdef0123456789abcdef01234567",
        "pipeline_id": "pipeline-42",
        "job_id": "job-7",
        "target": {
            "label": "qnx-arm64-release",
            "os": "QNX",
            "os_version": "7.1",
            "architecture": "aarch64",
            "build_type": "Release",
        },
        "compiler": {"driver": "qcc", "version": "8.3", "variant": "gcc_ntoaarch64le"},
        "conan": {"version": "2.8", "host_profile": "qnx-arm64", "build_profile": "linux-x86_64"},
        "paths": {"source_root": "/src", "generated_root": "/generated", "qnx_target": "/opt/qnx/target/qnx7"},
        "generated_code": {"version": "R2024b", "generation_id": "gen-1", "source_model_commit": "0123456789abcdef0123456789abcdef01234567"},
    }
    put(root / "identity" / "BUILD_IDENTITY.json", identity)
    put(root / "source" / "main.c", '#include "dep.h"\n#include <sys/neutrino.h>\nint main(void){return MODE;}\n')
    put(root / "source" / "config.h", "#define LOCAL_CONFIG 1\n")
    put(root / "qnx" / "usr" / "include" / "sys" / "neutrino.h", "#pragma once\n")
    put(root / "conan" / "export" / "dep" / "include" / "dep.h", "#pragma once\n")
    package = {
        "reference": "dep/1.0", "recipe_revision": "rrev1", "context": "host",
        "package_id": "abcdef1234567890", "package_revision": "prev1",
        "original_root": "/cache/dep", "logical_root": "conan://dep/1.0/abcdef1234567890",
        "exported_root": "conan/export/dep",
    }
    put(root / "conan" / "packages.json", {"schema_version": 1, "packages": [package]})
    put(root / "conan" / "conan-graph.json", {"graph": {"nodes": [{"ref": "dep/1.0#rrev1", "context": "host", "package_id": "abcdef1234567890"}]}})
    put(root / "compiler" / "qcc-c.macros.txt", "#define __QNX__ 1\n#define __AARCH64__ 1\n#define QNX_FUNC(x) ((x)+1)\n")
    put(root / "compiler" / "qcc-c.includes.txt", "#include <...> search starts here:\n /opt/qnx/target/qnx7/usr/include\nEnd of search list.\n")
    put(root / "compiler" / "qcc-variants.json", {"schema_version": 1, "variants": [{
        "variant": "gcc_ntoaarch64le", "language": "c",
        "macros_file": "compiler/qcc-c.macros.txt",
        "include_search_file": "compiler/qcc-c.includes.txt",
    }]})
    entries = [{
        "directory": "/src", "file": "/src/main.c",
        "arguments": ["qcc", "-Vgcc_ntoaarch64le", "-I/cache/dep/include", "--sysroot=/opt/qnx/target/qnx7", "-isystem", "=/usr/include", "-include", "/src/config.h", "-DMODE=1", "-MD", "-MF", "main.d", "-c", "/src/main.c"],
    }]
    put(root / "build" / "main.d", "main.o: /src/main.c /cache/dep/include/dep.h \\\n /opt/qnx/target/qnx7/usr/include/sys/neutrino.h\n/cache/dep/include/dep.h:\n")
    if two_profiles:
        put(root / "source" / "second.c", "int second(void){return MODE;}\n")
        entries.append({
            "directory": "/src", "file": "/src/second.c",
            "arguments": ["qcc", "-Vgcc_ntoaarch64le", "-I/cache/dep/include", "--sysroot=/opt/qnx/target/qnx7", "-isystem", "=/usr/include", "-DMODE=2", "-c", "/src/second.c"],
        })
    put(root / "compilation" / "compile_commands.json", entries)


class CollectorTests(unittest.TestCase):
    def run_collector(self, input_root, mode="strict"):
        output = input_root.parent / (input_root.name + "-out")
        code = collector.main(["--input-root", str(input_root), "--output-parent", str(output), "--mode", mode, "--no-zip"])
        packages = list(output.iterdir())
        self.assertEqual(1, len(packages))
        return code, packages[0]

    def test_nominal_strict_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "drop"
            valid_fixture(root)
            code, package = self.run_collector(root)
            self.assertEqual(0, code)
            report = json.loads((package / "collection-report.json").read_text())
            self.assertEqual("READY_FOR_ANALYSIS", report["status"])
            self.assertEqual(100.0, report["summary"]["source_coverage"]["coverage_percent"])
            profiles = json.loads((package / "cast-config" / "compilation-profiles.json").read_text())["profiles"]
            self.assertIn("__QNX__=1", [item["value"] for item in profiles[0]["macro_actions"]])
            self.assertIn("QNX_FUNC(x)=((x)+1)", [item["value"] for item in profiles[0]["macro_actions"]])
            units = json.loads((package / "cast-config" / "analysis-units.json").read_text())["analysis_units"]
            self.assertEqual("PACKAGE_ROOT", units[0]["path_base"])
            self.assertTrue(all(not value.startswith("/") for value in units[0]["includes"]))
            dependencies = (package / "cast-config" / "dependency-files.csv").read_text()
            self.assertNotIn("dep.h:\n", dependencies)

    def test_two_macro_sets_make_two_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "drop"
            valid_fixture(root, two_profiles=True)
            code, package = self.run_collector(root)
            self.assertEqual(0, code)
            profiles = json.loads((package / "cast-config" / "compilation-profiles.json").read_text())["profiles"]
            self.assertEqual(2, len(profiles))

    def test_duplicate_conan_identity_is_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "drop"
            valid_fixture(root)
            path = root / "conan" / "packages.json"
            data = json.loads(path.read_text())
            data["packages"].append(dict(data["packages"][0]))
            put(path, data)
            code, package = self.run_collector(root)
            self.assertEqual(2, code)
            self.assertEqual("NOT_QUALIFIED", json.loads((package / "collection-status.json").read_text())["status"])

    def test_missing_response_file_is_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "drop"
            valid_fixture(root)
            path = root / "compilation" / "compile_commands.json"
            data = json.loads(path.read_text())
            data[0]["arguments"].append("@missing.rsp")
            put(path, data)
            code, _ = self.run_collector(root)
            self.assertEqual(2, code)

    def test_response_file_is_expanded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "drop"
            valid_fixture(root)
            put(root / "compilation" / "response-files" / "flags.rsp", "-Vgcc_ntoaarch64le -I/cache/dep/include --sysroot=/opt/qnx/target/qnx7 -isystem =/usr/include -include /src/config.h -DMODE=1 -c /src/main.c\n")
            database = root / "compilation" / "compile_commands.json"
            data = json.loads(database.read_text())
            data[0]["arguments"] = ["qcc", "@flags.rsp"]
            put(database, data)
            code, package = self.run_collector(root)
            self.assertEqual(0, code)
            records = json.loads((package / "cast-config" / "compiler-invocations.json").read_text())
            self.assertEqual(["compilation/response-files/flags.rsp"], records[0]["response_files"])

    def test_forwarded_preprocessor_options(self):
        parsed = collector.parse_compiler_arguments(
            ["gcc", "-Wp,-DWP_FLAG=1,-I,/wp/include", "-Xpreprocessor", "-DXP_FLAG=2", "-dM", "-c", "/src/a.c"],
            "/src", "/src/a.c",
        )
        self.assertIn("WP_FLAG=1", parsed["macros"])
        self.assertIn("XP_FLAG=2", parsed["macros"])
        self.assertIn("/wp/include", parsed["include_paths"])
        self.assertEqual(["-dM"], parsed["preprocessor_dump_options"])

    def test_uncompiled_source_is_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "drop"
            valid_fixture(root)
            put(root / "source" / "unrepresented.c", "int not_in_target(void){return 0;}\n")
            code, package = self.run_collector(root)
            self.assertEqual(2, code)
            coverage = json.loads((package / "cast-config" / "source-coverage-summary.json").read_text())
            self.assertEqual(50.0, coverage["coverage_percent"])

    def test_cast_error_log_is_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "drop"
            valid_fixture(root)
            put(root / "cast-analysis-logs" / "analysis.log", "No such file or directory: dep_missing.h\n")
            code, package = self.run_collector(root)
            self.assertEqual(2, code)
            qualification = json.loads((package / "qualification.json").read_text())
            self.assertEqual("NOT_QUALIFIED", qualification["status"])

    def test_secret_pattern_in_log_is_not_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "drop"
            valid_fixture(root)
            put(root / "logs" / "build.log", "token=do-not-export-this\n")
            code, package = self.run_collector(root)
            self.assertEqual(0, code)
            copied_log = package / "evidence" / "logs" / "build.log"
            self.assertEqual("token=do-not-export-this\n", copied_log.read_text())

    def test_symlink_is_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "drop"
            valid_fixture(root)
            os.symlink(root / "source" / "main.c", root / "source" / "link.c")
            code, _ = self.run_collector(root)
            self.assertEqual(2, code)

    def test_exploratory_mode_returns_success_with_findings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "empty"
            root.mkdir()
            code, package = self.run_collector(root, "exploratory")
            self.assertEqual(0, code)
            self.assertEqual("EXPLORATORY", json.loads((package / "collection-status.json").read_text())["status"])

    def test_dependency_parser_ignores_mp_phony_rule(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.d"
            put(path, "a.o: a.c include/a.h \\\n include/b.h\ninclude/a.h:\ninclude/b.h:\n")
            rules = collector.parse_dependency_rules(path)
            self.assertEqual([("a.o", ["a.c", "include/a.h", "include/b.h"])], rules)

    def test_conan_graph_inventory_uses_exact_binary_reference(self):
        graph = {"graph": {"nodes": {"1": {
            "ref": "dep/1.0#rrev1", "context": "host",
            "package_id": "package123", "prev": "prev1",
        }}}}
        calls = []
        original = conan2_inventory.cache_path
        try:
            conan2_inventory.cache_path = lambda command, reference: calls.append((command, reference)) or "/cache/exact"
            inventory = conan2_inventory.make_inventory(graph, "conan-custom")
        finally:
            conan2_inventory.cache_path = original
        self.assertEqual([("conan-custom", "dep/1.0#rrev1:package123#prev1")], calls)
        self.assertEqual("/cache/exact", inventory["packages"][0]["original_root"])

    def test_client_export_then_cast_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "client-src"
            build = base / "client-build"
            cache = base / "conan-cache" / "dep"
            qnx = base / "qnx-target"
            probes = base / "probes"
            put(source / "main.c", "int main(void){return FLAG;}\n")
            put(cache / "include" / "dep.h", "#pragma once\n")
            put(qnx / "usr" / "include" / "sys" / "neutrino.h", "#pragma once\n")
            put(build / "main.d", f"main.o: {source / 'main.c'}\n")
            compile_db = base / "compile_commands.json"
            put(compile_db, [{
                "directory": str(source), "file": str(source / "main.c"),
                "arguments": ["qcc", "-Vgcc_ntoaarch64le", f"-I{cache / 'include'}", f"--sysroot={qnx}", "-isystem", "=/usr/include", "-DFLAG=0", "-c", str(source / "main.c")],
            }])
            graph = base / "graph.json"
            put(graph, {"graph": {"nodes": [{"ref": "dep/1.0#rrev", "context": "host", "package_id": "pid"}]}})
            packages = base / "packages.json"
            put(packages, {"schema_version": 1, "packages": [{
                "reference": "dep/1.0", "recipe_revision": "rrev", "context": "host",
                "package_id": "pid", "package_revision": "prev", "original_root": str(cache),
                "logical_root": "conan://dep/1.0/pid", "exported_root": "",
            }]})
            put(probes / "qcc-c.macros.txt", "#define __QNX__ 1\n")
            put(probes / "qcc-c.includes.txt", f"#include <...> search starts here:\n {qnx / 'usr' / 'include'}\nEnd of search list.\n")
            put(probes / "qcc-variants.json", {"schema_version": 1, "variants": [{
                "variant": "gcc_ntoaarch64le", "language": "c",
                "macros_file": "compiler/qcc-c.macros.txt", "include_search_file": "compiler/qcc-c.includes.txt",
            }]})
            bundle = base / "bundle"
            code = client_ci_export.main([
                "--bundle", str(bundle), "--source", str(source), "--build-root", str(build),
                "--compile-commands", str(compile_db), "--conan-packages", str(packages),
                "--conan-graph", str(graph), "--qnx-target", str(qnx), "--qcc-probe-dir", str(probes),
                "--application", "AppA", "--git-commit", "0123456789abcdef", "--pipeline-id", "42",
                "--target-label", "qnx-arm64", "--target-os", "QNX", "--target-os-version", "7.1",
                "--architecture", "aarch64", "--build-type", "Release", "--compiler", "qcc",
                "--compiler-variant", "gcc_ntoaarch64le", "--host-profile", "qnx-arm64",
                "--build-profile", "linux-x86_64", "--source-root-at-build", str(source),
                "--qnx-target-at-build", str(qnx),
            ])
            self.assertEqual(0, code)
            code, package = self.run_collector(bundle)
            self.assertEqual(0, code)
            self.assertEqual("READY_FOR_ANALYSIS", json.loads((package / "collection-status.json").read_text())["status"])

    def test_client_log_is_copied_without_redaction(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "build.log"
            destination = Path(temp) / "bundle" / "build.log"
            put(source, "starting\ntoken=super-secret-value\nfinished\n")
            client_ci_export.copy_log(source, destination)
            exported = destination.read_text()
            self.assertEqual("starting\ntoken=super-secret-value\nfinished\n", exported)

    def test_internal_header_symlink_is_materialized(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlink unavailable")
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "cache"
            destination = Path(temp) / "export"
            put(source / "real" / "dep.h", "#pragma once\n")
            os.symlink(source / "real" / "dep.h", source / "alias.h")
            copied = client_ci_export.copy_selected(source, destination, client_ci_export.HEADER_EXTENSIONS)
            self.assertEqual(2, copied)
            self.assertFalse((destination / "alias.h").is_symlink())
            self.assertEqual("#pragma once\n", (destination / "alias.h").read_text())

    def test_identical_baseline_has_no_semantic_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "drop"
            valid_fixture(root)
            first_parent = base / "first"
            first_code = collector.main(["--input-root", str(root), "--output-parent", str(first_parent), "--mode", "strict", "--no-zip"])
            self.assertEqual(0, first_code)
            first_package = next(first_parent.iterdir())
            second_parent = base / "second"
            second_code = collector.main([
                "--input-root", str(root), "--output-parent", str(second_parent),
                "--mode", "strict", "--baseline", str(first_package), "--no-zip",
            ])
            self.assertEqual(0, second_code)
            second_package = next(second_parent.iterdir())
            diff = json.loads((second_package / "cast-config" / "baseline-diff.json").read_text())
            self.assertEqual([], diff["changes"])


if __name__ == "__main__":
    unittest.main()

