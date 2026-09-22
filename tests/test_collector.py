import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cast_offline_collector as collector
import client_ci_export
import conan2_inventory
import makefile_local_audit
import makefile_local_export
import makefile_local_recover


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
    def make_symlink_or_skip(self, target, link):
        if not hasattr(os, "symlink"):
            self.skipTest("symlink unavailable")
        try:
            os.symlink(target, link)
        except OSError as error:
            self.skipTest(f"symlink creation unavailable: {error}")

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
            self.assertFalse((package / "cast-config" / "cast-include-paths.absolute.txt").exists())
            for relative in (
                "cast-config/analysis-units.json",
                "cast-config/compilation-profiles.json",
                "cast-config/compiler-invocations.json",
                "cast-config/path-remapping.csv",
                "cast-config/source-coverage.csv",
                "cast-config/conan-dependencies.csv",
            ):
                self.assertNotIn(str(package), (package / relative).read_text())

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
            self.make_symlink_or_skip(root / "source" / "main.c", root / "source" / "link.c")
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

    def test_makefile_local_export_then_cast_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            staged = base / "staging"
            bundle = base / "CAST_DELIVERABLES_BUNDLE"
            valid_fixture(staged)
            code = makefile_local_export.main([
                "--staged-root", str(staged),
                "--bundle", str(bundle),
            ])
            self.assertEqual(0, code)
            code, package = self.run_collector(bundle)
            self.assertEqual(0, code)
            self.assertEqual("READY_FOR_ANALYSIS", json.loads((package / "collection-status.json").read_text())["status"])

    def test_makefile_local_audit_flags_raw_cmake_build_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "LCCS_Archive_CAST"
            put(root / "App" / "build" / "CMakeCache.txt", "CMAKE_EXPORT_COMPILE_COMMANDS:BOOL=\n")
            put(root / "App" / "build" / "CMakeFiles" / "App.dir" / "flags.make", "C_DEFINES = -DDEBUG\n")
            put(root / "App" / "build" / "CMakeFiles" / "App.dir" / "build.make", "App.o: src/App.c\n")
            put(root / "App" / "build" / "CMakeFiles" / "App.dir" / "src" / "App.c.o.d", "App.o: src/App.c include/App.h\n")
            put(root / "App" / "build" / "conanbuildinfo.txt", "[requires]\ndep/1.0\n")
            report = makefile_local_audit.audit(makefile_local_audit.snapshot_from_root(root))
            self.assertEqual("DIAGNOSTIC_ONLY", report["status"])
            self.assertIn("compile database", report["missing"])
            self.assertIn("compiler macros probes", report["missing"])
            self.assertTrue(any("CMAKE_EXPORT_COMPILE_COMMANDS" in warning for warning in report["warnings"]))

    def test_makefile_local_recover_creates_partial_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "LCCS_Archive_CAST"
            app = root / "App"
            output = base / "recovered"
            put(app / "src" / "App.c", "int app(void){return 0;}\n")
            put(app / "include" / "App.h", "#pragma once\n")
            put(root / ".conan" / "data" / "dep" / "1.0" / "_" / "_" / "package" / "abc123" / "include" / "dep.h", "#pragma once\n")
            put(app / "build" / "CMakeFiles" / "App.dir" / "flags.make", "C_DEFINES = -DDEBUG\nC_INCLUDES = -IC:/cache/dep/include -isystem =/usr/include -include C:/work/App/include/App.h\nC_FLAGS = -Vgcc_ntoarmv7le -g\n")
            put(
                app / "build" / "CMakeFiles" / "App.dir" / "build.make",
                "CMAKE_SOURCE_DIR = C:/work/App\n"
                "CMAKE_BINARY_DIR = C:/work/App/build\n"
                "\tC:/qnx/usr/bin/myCMakeQCC.bat $(C_DEFINES) $(C_INCLUDES) $(C_FLAGS) -MD -MT App.o -MF App.o.d -o App.o -c C:/work/App/src/App.c\n",
            )
            put(app / "build" / "CMakeFiles" / "App.dir" / "src" / "App.c.o.d", "App.o: C:/work/App/src/App.c C:/cache/dep/include/dep.h\n")
            put(app / "build" / "conaninfo.txt", "[full_requires]\n    dep/1.0:abc123\n")
            put(app / "build" / "conanbuildinfo.txt", "[rootpath_dep]\nC:/cache/.conan/data/dep/1.0/_/_/package/abc123\n")
            code = makefile_local_recover.main(["--root", str(root), "--output", str(output)])
            self.assertEqual(2, code)
            commands = json.loads((output / "compilation" / "compile_commands.json").read_text())
            self.assertEqual(1, len(commands))
            self.assertIn("-DDEBUG", commands[0]["arguments"])
            self.assertEqual("App/build", commands[0]["directory"])
            self.assertEqual("source/App/src/App.c", commands[0]["file"])
            self.assertEqual("myCMakeQCC.bat", commands[0]["arguments"][0])
            self.assertFalse(any(re.search(r"(^|[=\s])([A-Za-z]:/|/)", value.replace("\\", "/")) for value in commands[0]["arguments"]))
            self.assertIn("-Iunresolved/cache/dep/include", commands[0]["arguments"])
            self.assertIn("sysroot-relative/usr/include", commands[0]["arguments"])
            self.assertIn("source/App/include/App.h", commands[0]["arguments"])
            packages = json.loads((output / "conan" / "packages.recovered.json").read_text())["packages"]
            self.assertEqual("dep/1.0", packages[0]["reference"])
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertEqual("RECOVERED_PARTIAL", report["status"])
            self.assertEqual(".", report["root"])
            self.assertEqual(".", report["output"])
            self.assertEqual(["App"], report["applications_detected"])
            self.assertEqual(2, report["copied_source_files"])
            root_listing = (output / "root-build-files.json").read_text()
            self.assertNotIn(str(root), root_listing)
            self.assertNotIn(str(output), root_listing)

    def test_makefile_local_recover_filters_one_application(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "LCCS_Archive_CAST"
            output = base / "recovered"
            put(root / "EQT_SP_CAN" / "build" / "CMakeFiles" / "EQT_SP_CAN.dir" / "build.make", "")
            put(root / "EQT_SP_CAN" / "build" / "CMakeFiles" / "EQT_SP_CAN.dir" / "flags.make", "")
            put(root / "EQT_SP_CAN" / "build" / "CMakeFiles" / "EQT_SP_CAN.dir" / "src" / "App.c.o.d", "")
            put(root / "EQT_SP_CAN" / "src" / "App.c", "")
            put(root / "EQT_SP_CAN" / "include" / "App.h", "")
            put(root / "Other" / "build" / "CMakeFiles" / "Other.dir" / "build.make", "")
            code = makefile_local_recover.main([
                "--root", str(root),
                "--application", "EQT_SP_CAN",
                "--output", str(output),
            ])
            self.assertEqual(2, code)
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertEqual("RECOVERED_PARTIAL", report["status"])
            self.assertEqual(["EQT_SP_CAN"], report["applications_detected"])
            self.assertEqual(3, report["applications"]["EQT_SP_CAN"]["build_files"])
            self.assertEqual(2, report["applications"]["EQT_SP_CAN"]["source_files"])
            listed = json.loads((output / "root-build-files.json").read_text())
            self.assertEqual(["EQT_SP_CAN"], listed["applications_detected"])

    def test_makefile_local_recover_filters_conan_headers_by_selected_application(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "LCCS_Archive_CAST"
            output = base / "recovered"
            put(root / "EQT_SP_CAN" / "build" / "conaninfo.txt", "[full_requires]\n    dep/1.0:abc123\n")
            put(root / "EQT_SP_CAN" / "build" / "conanbuildinfo.txt", "[rootpath_dep]\nC:/cache/.conan/data/dep/1.0/_/_/package/abc123\n")
            put(root / "Other" / "build" / "conaninfo.txt", "[full_requires]\n    other/2.0:def456\n")
            put(root / "Other" / "build" / "conanbuildinfo.txt", "[rootpath_other]\nC:/cache/.conan/data/other/2.0/_/_/package/def456\n")
            put(root / ".conan" / "data" / "dep" / "1.0" / "_" / "_" / "package" / "abc123" / "include" / "dep.h", "#pragma once\n")
            put(root / ".conan" / "data" / "other" / "2.0" / "_" / "_" / "package" / "def456" / "include" / "other.h", "#pragma once\n")
            code = makefile_local_recover.main([
                "--root", str(root),
                "--application", "EQT_SP_CAN",
                "--output", str(output),
            ])
            self.assertEqual(2, code)
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertEqual(1, report["copied_conan_header_files"])
            self.assertTrue((output / "conan" / "export-recovered" / ".conan" / "data" / "dep" / "1.0" / "_" / "_" / "package" / "abc123" / "include" / "dep.h").is_file())
            self.assertFalse((output / "conan" / "export-recovered" / ".conan" / "data" / "other" / "2.0" / "_" / "_" / "package" / "def456" / "include" / "other.h").exists())

    def test_makefile_local_recover_copies_root_compiler_probes(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "LCCS_Archive_CAST"
            output = base / "recovered"
            put(root / "EQT_SP_CAN" / "build" / "CMakeFiles" / "EQT_SP_CAN.dir" / "build.make", "")
            put(root / "EQT_SP_CAN" / "src" / "App.c", "")
            put(root / "compiler" / "qcc-variants.json", {"schema_version": 1, "variants": []})
            put(root / "compiler" / "ntoarm.macros.txt", "#define X 1\n")
            put(root / "compiler" / "ntoarm.includes.txt", "/qnx/target/usr/include\n")
            code = makefile_local_recover.main([
                "--root", str(root),
                "--output", str(output),
            ])
            self.assertEqual(2, code)
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertEqual(3, report["compiler_probe_files"])
            self.assertEqual(3, report["copied_probe_files"])
            self.assertTrue((output / "compiler" / "qcc-variants.json").is_file())

    def test_makefile_local_recover_detects_all_root_applications(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "LCCS_Archive_CAST"
            output = base / "recovered"
            put(root / "EQT_ALRT" / "build" / "CMakeFiles" / "EQT_ALRT.dir" / "build.make", "")
            put(root / "EQT_ALRT" / "build" / "CMakeFiles" / "EQT_ALRT.dir" / "flags.make", "")
            put(root / "EQT_ALRT" / "src" / "EQT_ALRT.c", "")
            put(root / "EQT_CAERO" / "build" / "CMakeFiles" / "EQT_CAERO.dir" / "build.make", "")
            put(root / "EQT_CAERO" / "build" / "CMakeFiles" / "EQT_CAERO.dir" / "src" / "EQT_CAERO.c.o.d", "")
            put(root / "EQT_CAERO" / "src" / "EQT_CAERO.c", "")
            put(root / ".conan" / "data" / "Core" / "70.0.0" / "_" / "_" / "package" / "abc" / "include" / "core.h", "")
            code = makefile_local_recover.main([
                "--root", str(root),
                "--output", str(output),
            ])
            self.assertEqual(2, code)
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertEqual(["EQT_ALRT", "EQT_CAERO"], report["applications_detected"])
            self.assertEqual(2, report["applications_count"])
            self.assertEqual(2, report["applications"]["EQT_ALRT"]["build_files"])
            self.assertEqual(2, report["applications"]["EQT_CAERO"]["build_files"])

    def test_client_log_is_copied_without_redaction(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "build.log"
            destination = Path(temp) / "bundle" / "build.log"
            put(source, "starting\ntoken=super-secret-value\nfinished\n")
            client_ci_export.copy_log(source, destination)
            exported = destination.read_text()
            self.assertEqual("starting\ntoken=super-secret-value\nfinished\n", exported)

    def test_internal_header_symlink_is_materialized(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "cache"
            destination = Path(temp) / "export"
            put(source / "real" / "dep.h", "#pragma once\n")
            self.make_symlink_or_skip(source / "real" / "dep.h", source / "alias.h")
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

