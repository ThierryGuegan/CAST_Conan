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
            root = base / "PROJECT_Archive_CAST"
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
            root = base / "PROJECT_Archive_CAST"
            app = root / "App"
            output = base / "recovered"
            put(app / "src" / "App.c", "int app(void){return 0;}\n")
            put(app / "include" / "App.h", "#pragma once\n")
            put(root / ".conan" / "data" / "Core" / "70.0.0" / "_" / "_" / "package" / "6d36175b7858b9d34e4cfb4c578ae50b8e54a65f" / "include" / "core.h", "#pragma once\n")
            put(root / ".conan" / "data" / "Core" / "70.0.0" / "_" / "_" / "package" / "6d36175b7858b9d34e4cfb4c578ae50b8e54a65f" / "lib" / "core.a", "")
            put(root / ".conan" / "data" / "project-bus" / "70.0.0" / "_" / "_" / "package" / "5ab84d6acfe1f23c4fae0ab88f26e3a396351ac9" / "include" / "bus.h", "#pragma once\n")
            put(root / ".conan" / "data" / "libQNX" / "7.0.2" / "_" / "_" / "package" / "20bb78565db6a88fe4d4f78dcb593054d3916036" / "include" / "qnx.h", "#pragma once\n")
            put(root / ".conan" / "data" / "libresource" / "3.1.1" / "_" / "_" / "package" / "20bb78565db6a88fe4d4f78dcb593054d3916036" / "include" / "resource.h", "#pragma once\n")
            put(
                app / "build" / "CMakeFiles" / "App.dir" / "flags.make",
                "C_DEFINES = -DDEBUG\n"
                "C_INCLUDES = "
                "-Iunresolved/Users/DEVUSER/Documents/Workspace/.conan/data/Core/70.0.0/*/*/package/6d36175b7858b9d34e4cfb4c578ae50b8e54a65f/include "
                "-Iunresolved/Users/DEVUSER/Documents/Workspace/.conan/data/project-bus/70.0.0/_/_/package/5ab84d6acfe1f23c4fae0ab88f26e3a396351ac9/include "
                "-Iunresolved/Users/DEVUSER/Documents/Workspace/.conan/data/libresource/3.1.1/*/*/package/20bb78565db6a88fe4d4f78dcb593054d3916036/include "
                "-Iunresolved/Users/DEVUSER/Documents/Workspace/.conan/data/libQNX/7.0.2/*/*/package/20bb78565db6a88fe4d4f78dcb593054d3916036/include "
                "-isystem =/usr/include -include C:/work/App/include/App.h\n"
                "C_FLAGS = -Vgcc_ntoarmv7le -g\n",
            )
            put(
                app / "build" / "CMakeFiles" / "App.dir" / "build.make",
                "CMAKE_SOURCE_DIR = C:/work/App\n"
                "CMAKE_BINARY_DIR = C:/work/App/build\n"
                "\tC:/qnx/usr/bin/myCMakeQCC.bat $(C_DEFINES) $(C_INCLUDES) $(C_FLAGS) -MD -MT App.o -MF App.o.d -o App.o -c C:/work/App/src/App.c\n",
            )
            put(app / "build" / "CMakeFiles" / "App.dir" / "src" / "App.c.o.d", "App.o: C:/work/App/src/App.c C:/cache/dep/include/dep.h\n")
            put(app / "build" / "CMakeFiles" / "App.dir" / "src" / "App.c.o", "")
            put(
                app / "build" / "conaninfo.txt",
                "[full_requires]\n"
                "    Core/70.0.0:6d36175b7858b9d34e4cfb4c578ae50b8e54a65f\n"
                "    project-bus/70.0.0:5ab84d6acfe1f23c4fae0ab88f26e3a396351ac9\n"
                "    libQNX/7.0.2:20bb78565db6a88fe4d4f78dcb593054d3916036\n"
                "    libresource/3.1.1:20bb78565db6a88fe4d4f78dcb593054d3916036\n",
            )
            put(
                app / "build" / "conanbuildinfo.txt",
                "[rootpath_Core]\n"
                "C:/Users/DEVUSER/.conan/data/Core/70.0.0/*/*/package/6d36175b7858b9d34e4cfb4c578ae50b8e54a65f\n"
                "[rootpath_project-bus]\n"
                "C:/Users/DEVUSER/.conan/data/project-bus/70.0.0/_/_/package/5ab84d6acfe1f23c4fae0ab88f26e3a396351ac9\n"
                "[rootpath_libQNX]\n"
                "C:/Users/DEVUSER/.conan/data/libQNX/7.0.2/_/_/package/20bb78565db6a88fe4d4f78dcb593054d3916036\n"
                "[rootpath_libresource]\n"
                "C:/Users/DEVUSER/.conan/data/libresource/3.1.1/_/_/package/20bb78565db6a88fe4d4f78dcb593054d3916036\n",
            )
            code = makefile_local_recover.main(["--root", str(root), "--output", str(output)])
            self.assertEqual(0, code)
            commands = json.loads((output / "compilation" / "compile_commands.json").read_text())
            self.assertEqual(1, len(commands))
            self.assertIn("-DDEBUG", commands[0]["arguments"])
            self.assertEqual("App/build", commands[0]["directory"])
            self.assertEqual("source/App/src/App.c", commands[0]["file"])
            self.assertEqual("myCMakeQCC.bat", commands[0]["arguments"][0])
            self.assertFalse(any(re.search(r"(^|[=\s])([A-Za-z]:/|/)", value.replace("\\", "/")) for value in commands[0]["arguments"]))
            self.assertIn("-Iconan/export-recovered/.conan/data/Core/70.0.0/_/_/package/6d36175b7858b9d34e4cfb4c578ae50b8e54a65f/include", commands[0]["arguments"])
            self.assertIn("-Iconan/export-recovered/.conan/data/project-bus/70.0.0/_/_/package/5ab84d6acfe1f23c4fae0ab88f26e3a396351ac9/include", commands[0]["arguments"])
            self.assertIn("-Iconan/export-recovered/.conan/data/libQNX/7.0.2/_/_/package/20bb78565db6a88fe4d4f78dcb593054d3916036/include", commands[0]["arguments"])
            self.assertIn("-Iconan/export-recovered/.conan/data/libresource/3.1.1/_/_/package/20bb78565db6a88fe4d4f78dcb593054d3916036/include", commands[0]["arguments"])
            self.assertIn("sysroot-relative/usr/include", commands[0]["arguments"])
            self.assertIn("source/App/include/App.h", commands[0]["arguments"])
            packages = json.loads((output / "conan" / "packages.recovered.json").read_text())["packages"]
            self.assertEqual(
                [
                    ".conan/data/Core/70.0.0/*/*/package/6d36175b7858b9d34e4cfb4c578ae50b8e54a65f",
                    ".conan/data/project-bus/70.0.0/_/_/package/5ab84d6acfe1f23c4fae0ab88f26e3a396351ac9",
                    ".conan/data/libQNX/7.0.2/_/_/package/20bb78565db6a88fe4d4f78dcb593054d3916036",
                    ".conan/data/libresource/3.1.1/_/_/package/20bb78565db6a88fe4d4f78dcb593054d3916036",
                ],
                [item["original_root"] for item in packages],
            )
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertEqual("RECOVERED_WITH_LOCAL_SUPPLEMENTS", report["status"])
            self.assertNotIn("QCC macro/include probes", report["missing_for_strict_cast"])
            self.assertEqual(3, report["recovered_probe_placeholders"])
            self.assertEqual(".", report["root"])
            self.assertEqual(".", report["output"])
            self.assertEqual(["App"], report["applications_detected"])
            self.assertEqual(2, report["copied_source_files"])
            root_listing = (output / "root-build-files.json").read_text()
            self.assertNotIn(str(root), root_listing)
            self.assertNotIn(str(output), root_listing)
            self.assertTrue((output / "compiler" / "gcc_ntoarmv7le-c.macros.txt").is_file())
            self.assertTrue((output / "compiler" / "gcc_ntoarmv7le-c.includes.txt").is_file())
            variants = json.loads((output / "compiler" / "qcc-variants.json").read_text())["variants"]
            self.assertEqual("reconstructed_from_compile_commands", variants[0]["recovery"])
            self.assertIn("#define DEBUG 1", (output / "compiler" / "gcc_ntoarmv7le-c.macros.txt").read_text())
            includes_text = (output / "compiler" / "gcc_ntoarmv7le-c.includes.txt").read_text()
            self.assertIn("conan/export-recovered/.conan/data/Core/70.0.0/_/_/package/6d36175b7858b9d34e4cfb4c578ae50b8e54a65f/include", includes_text)
            self.assertIn("conan/export-recovered/.conan/data/project-bus/70.0.0/_/_/package/5ab84d6acfe1f23c4fae0ab88f26e3a396351ac9/include", includes_text)
            self.assertIn("conan/export-recovered/.conan/data/libQNX/7.0.2/_/_/package/20bb78565db6a88fe4d4f78dcb593054d3916036/include", includes_text)
            self.assertIn("conan/export-recovered/.conan/data/libresource/3.1.1/_/_/package/20bb78565db6a88fe4d4f78dcb593054d3916036/include", includes_text)
            self.assertTrue((output / "conan" / "export-recovered" / ".conan" / "data" / "Core" / "70.0.0" / "_" / "_" / "package" / "6d36175b7858b9d34e4cfb4c578ae50b8e54a65f" / "include" / "core.h").is_file())
            self.assertTrue((output / "conan" / "export-recovered" / ".conan" / "data" / "project-bus" / "70.0.0" / "_" / "_" / "package" / "5ab84d6acfe1f23c4fae0ab88f26e3a396351ac9" / "include" / "bus.h").is_file())
            self.assertTrue((output / "conan" / "export-recovered" / ".conan" / "data" / "libQNX" / "7.0.2" / "_" / "_" / "package" / "20bb78565db6a88fe4d4f78dcb593054d3916036" / "include" / "qnx.h").is_file())
            self.assertTrue((output / "conan" / "export-recovered" / ".conan" / "data" / "libresource" / "3.1.1" / "_" / "_" / "package" / "20bb78565db6a88fe4d4f78dcb593054d3916036" / "include" / "resource.h").is_file())
            mapping = json.loads((output / "copy-mapping.json").read_text())
            self.assertEqual(report["copied_mapping_files"], mapping["copied_count"])
            self.assertTrue(all(not Path(item["source"]).is_absolute() for item in mapping["copied"]))
            self.assertTrue(all(not Path(item["destination"]).is_absolute() for item in mapping["copied"]))
            self.assertIn({
                "category": "source",
                "source": "App/src/App.c",
                "destination": "source/App/src/App.c",
            }, mapping["copied"])
            self.assertIn({
                "category": "conan_headers",
                "source": ".conan/data/Core/70.0.0/_/_/package/6d36175b7858b9d34e4cfb4c578ae50b8e54a65f/include/core.h",
                "destination": "conan/export-recovered/.conan/data/Core/70.0.0/_/_/package/6d36175b7858b9d34e4cfb4c578ae50b8e54a65f/include/core.h",
            }, mapping["copied"])
            coverage = json.loads((output / "copy-coverage.json").read_text())
            self.assertEqual(report["root_files"], coverage["root_files"])
            self.assertGreater(coverage["root_files"], mapping["copied_count"])
            self.assertEqual(mapping["copied_count"], coverage["reason_counts"]["copied_from_root"])
            self.assertGreaterEqual(coverage["reason_counts"]["dependency_trace_not_copied_optional_absolute_paths"], 1)
            self.assertGreaterEqual(coverage["reason_counts"]["build_artifact_not_required_for_recovery"], 1)
            self.assertGreaterEqual(coverage["reason_counts"]["conan_cache_non_header_or_binary_artifact"], 1)
            self.assertIn("compilation/compile_commands.json", coverage["generated_output_samples"])

    def test_makefile_local_recover_keeps_compile_commands_with_unresolved_absolute_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "PROJECT_Archive_CAST"
            app = root / "App"
            output = base / "recovered"
            put(app / "src" / "App.c", "int app(void){return 0;}\n")
            put(app / "build" / "CMakeFiles" / "App.dir" / "flags.make", "C_INCLUDES = -IC:/missing/vendor/include -isystem=/usr/include\nC_FLAGS = -Vgcc_ntoarmv7le\n")
            put(
                app / "build" / "CMakeFiles" / "App.dir" / "build.make",
                "CMAKE_SOURCE_DIR = C:/work/App\n"
                "CMAKE_BINARY_DIR = C:/work/App/build\n"
                "\tC:/qnx/usr/bin/myCMakeQCC.bat $(C_INCLUDES) $(C_FLAGS) -c C:/work/App/src/App.c\n",
            )
            code = makefile_local_recover.main(["--root", str(root), "--output", str(output)])
            self.assertEqual(2, code)
            commands_path = output / "compilation" / "compile_commands.json"
            self.assertTrue(commands_path.is_file())
            commands = json.loads(commands_path.read_text())
            self.assertEqual(1, len(commands))
            self.assertIn("-Iunresolved/missing/vendor/include", commands[0]["arguments"])
            self.assertIn("-isystem=unresolved/usr/include", commands[0]["arguments"])
            self.assertNotIn("-isystemsysroot-relative/usr/include", commands[0]["arguments"])
            self.assertEqual("source/App/src/App.c", commands[0]["arguments"][commands[0]["arguments"].index("-c") + 1])

    def test_makefile_local_recover_reconstructs_unknown_qcc_probe_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "PROJECT_Archive_CAST"
            app = root / "App"
            output = base / "recovered"
            put(app / "src" / "App.c", "int app(void){return FEATURE;}\n")
            put(app / "build" / "CMakeFiles" / "App.dir" / "flags.make", "C_DEFINES = -DFEATURE=1\nC_INCLUDES = -Iinclude\nC_FLAGS = -g\n")
            put(
                app / "build" / "CMakeFiles" / "App.dir" / "build.make",
                "CMAKE_SOURCE_DIR = C:/work/App\n"
                "CMAKE_BINARY_DIR = C:/work/App/build\n"
                "\tC:/qnx/usr/bin/myCMakeQCC.bat $(C_DEFINES) $(C_INCLUDES) $(C_FLAGS) -c C:/work/App/src/App.c\n",
            )
            code = makefile_local_recover.main(["--root", str(root), "--output", str(output)])
            self.assertEqual(2, code)
            variants = json.loads((output / "compiler" / "qcc-variants.json").read_text())["variants"]
            self.assertEqual("unknown-qcc", variants[0]["variant"])
            self.assertIn("#define FEATURE 1", (output / "compiler" / "unknown-qcc-c.macros.txt").read_text())
            self.assertIn("include", (output / "compiler" / "unknown-qcc-c.includes.txt").read_text())

    def test_makefile_local_recover_uses_real_source_after_repeated_c_option(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "PROJECT_Archive_CAST"
            app = root / "App"
            output = base / "recovered"
            put(app / "src" / "App.c", "int app(void){return 0;}\n")
            put(app / "build" / "CMakeFiles" / "App.dir" / "flags.make", "C_FLAGS = -Vgcc_ntoarmv7le -c -Wc,-Wall\n")
            put(
                app / "build" / "CMakeFiles" / "App.dir" / "build.make",
                "CMAKE_SOURCE_DIR = C:/work/App\n"
                "CMAKE_BINARY_DIR = C:/work/App/build\n"
                "\tC:/qnx/usr/bin/myCMakeQCC.bat $(C_FLAGS) -o App.o -c C:/work/App/src/App.c\n",
            )
            code = makefile_local_recover.main(["--root", str(root), "--output", str(output)])
            self.assertEqual(2, code)
            commands = json.loads((output / "compilation" / "compile_commands.json").read_text())
            self.assertEqual("source/App/src/App.c", commands[0]["file"])
            self.assertIn("-Wc,-Wall", commands[0]["arguments"])

    def test_makefile_local_recover_copies_nested_application_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "PROJECT_Archive_CAST"
            app = root / "Core"
            output = base / "recovered"
            put(app / "SF" / "Services" / "src" / "bus.c", "int bus(void){return 0;}\n")
            put(app / "SF" / "Services" / "include" / "bus.h", "#pragma once\n")
            put(app / "SF" / "arch" / "include" / "arch.h", "#pragma once\n")
            put(
                app / "build" / "CMakeFiles" / "Core.dir" / "flags.make",
                "C_INCLUDES = -IC:/Users/DEVUSER/Documents/Workspace/Core/./SF/Services/include "
                "-Iunresolved/Users/DEVUSER/Documents/Workspace/Core/./SF/arch/include\n"
                "C_FLAGS = -Vgcc_ntoarmv7le\n",
            )
            put(
                app / "build" / "CMakeFiles" / "Core.dir" / "build.make",
                "CMAKE_SOURCE_DIR = C:/Users/DEVUSER/Documents/Workspace/Core\n"
                "CMAKE_BINARY_DIR = C:/Users/DEVUSER/Documents/Workspace/Core/build\n"
                "\tC:/qnx/usr/bin/myCMakeQCC.bat $(C_INCLUDES) $(C_FLAGS) -c C:/Users/DEVUSER/Documents/Workspace/Core/./SF/Services/src/bus.c\n",
            )
            code = makefile_local_recover.main(["--root", str(root), "--output", str(output)])
            self.assertEqual(2, code)
            commands = json.loads((output / "compilation" / "compile_commands.json").read_text())
            self.assertEqual("source/Core/SF/Services/src/bus.c", commands[0]["file"])
            self.assertIn("-Isource/Core/SF/Services/include", commands[0]["arguments"])
            self.assertIn("-Isource/Core/SF/arch/include", commands[0]["arguments"])
            self.assertTrue((output / "source" / "Core" / "SF" / "Services" / "src" / "bus.c").is_file())
            self.assertTrue((output / "source" / "Core" / "SF" / "Services" / "include" / "bus.h").is_file())
            self.assertTrue((output / "source" / "Core" / "SF" / "arch" / "include" / "arch.h").is_file())

    def test_makefile_local_recover_keeps_conan_user_channel_disambiguation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "PROJECT_Archive_CAST"
            app = root / "App"
            output = base / "recovered"
            package_id = "abc123"
            put(app / "src" / "App.c", "int app(void){return 0;}\n")
            put(root / ".conan" / "data" / "dep" / "1.0" / "stable" / "prod" / "package" / package_id / "include" / "dep.h", "#pragma once\n")
            put(root / ".conan" / "data" / "dep" / "1.0" / "testing" / "dev" / "package" / package_id / "include" / "dep.h", "#pragma once\n")
            put(
                app / "build" / "CMakeFiles" / "App.dir" / "flags.make",
                f"C_INCLUDES = -IC:/cache/.conan/data/dep/1.0/testing/dev/package/{package_id}/include\n"
                "C_FLAGS = -Vgcc_ntoarmv7le\n",
            )
            put(
                app / "build" / "CMakeFiles" / "App.dir" / "build.make",
                "CMAKE_SOURCE_DIR = C:/work/App\n"
                "CMAKE_BINARY_DIR = C:/work/App/build\n"
                "\tC:/qnx/usr/bin/myCMakeQCC.bat $(C_INCLUDES) $(C_FLAGS) -c C:/work/App/src/App.c\n",
            )
            put(app / "build" / "conaninfo.txt", f"[full_requires]\n    dep/1.0:{package_id}\n")
            put(app / "build" / "conanbuildinfo.txt", f"[rootpath_dep]\nC:/cache/.conan/data/dep/1.0/testing/dev/package/{package_id}\n")
            code = makefile_local_recover.main(["--root", str(root), "--output", str(output)])
            self.assertEqual(0, code)
            commands = json.loads((output / "compilation" / "compile_commands.json").read_text())
            self.assertIn(
                f"-Iconan/export-recovered/.conan/data/dep/1.0/testing/dev/package/{package_id}/include",
                commands[0]["arguments"],
            )
            self.assertTrue((output / "conan" / "export-recovered" / ".conan" / "data" / "dep" / "1.0" / "testing" / "dev" / "package" / package_id / "include" / "dep.h").is_file())
            self.assertFalse((output / "conan" / "export-recovered" / ".conan" / "data" / "dep" / "1.0" / "stable" / "prod" / "package" / package_id / "include" / "dep.h").exists())

    def test_makefile_local_recover_filters_one_application(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "PROJECT_Archive_CAST"
            output = base / "recovered"
            put(root / "APP_ALPHA" / "build" / "CMakeFiles" / "APP_ALPHA.dir" / "build.make", "")
            put(root / "APP_ALPHA" / "build" / "CMakeFiles" / "APP_ALPHA.dir" / "flags.make", "")
            put(root / "APP_ALPHA" / "build" / "CMakeFiles" / "APP_ALPHA.dir" / "src" / "App.c.o.d", "")
            put(root / "APP_ALPHA" / "src" / "App.c", "")
            put(root / "APP_ALPHA" / "include" / "App.h", "")
            put(root / "Other" / "build" / "CMakeFiles" / "Other.dir" / "build.make", "")
            code = makefile_local_recover.main([
                "--root", str(root),
                "--application", "APP_ALPHA",
                "--output", str(output),
            ])
            self.assertEqual(2, code)
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertEqual("RECOVERED_PARTIAL", report["status"])
            self.assertEqual(["APP_ALPHA"], report["applications_detected"])
            self.assertEqual(3, report["applications"]["APP_ALPHA"]["build_files"])
            self.assertEqual(2, report["applications"]["APP_ALPHA"]["source_files"])
            listed = json.loads((output / "root-build-files.json").read_text())
            self.assertEqual(["APP_ALPHA"], listed["applications_detected"])

    def test_makefile_local_recover_filters_conan_headers_by_selected_application(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "PROJECT_Archive_CAST"
            output = base / "recovered"
            put(root / "APP_ALPHA" / "build" / "conaninfo.txt", "[full_requires]\n    dep/1.0:abc123\n")
            put(root / "APP_ALPHA" / "build" / "conanbuildinfo.txt", "[rootpath_dep]\nC:/cache/.conan/data/dep/1.0/_/_/package/abc123\n")
            put(root / "Other" / "build" / "conaninfo.txt", "[full_requires]\n    other/2.0:def456\n")
            put(root / "Other" / "build" / "conanbuildinfo.txt", "[rootpath_other]\nC:/cache/.conan/data/other/2.0/_/_/package/def456\n")
            put(root / ".conan" / "data" / "dep" / "1.0" / "_" / "_" / "package" / "abc123" / "include" / "dep.h", "#pragma once\n")
            put(root / ".conan" / "data" / "other" / "2.0" / "_" / "_" / "package" / "def456" / "include" / "other.h", "#pragma once\n")
            code = makefile_local_recover.main([
                "--root", str(root),
                "--application", "APP_ALPHA",
                "--output", str(output),
            ])
            self.assertEqual(2, code)
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertEqual(1, report["copied_conan_header_files"])
            self.assertTrue((output / "conan" / "export-recovered" / ".conan" / "data" / "dep" / "1.0" / "_" / "_" / "package" / "abc123" / "include" / "dep.h").is_file())
            self.assertFalse((output / "conan" / "export-recovered" / ".conan" / "data" / "other" / "2.0" / "_" / "_" / "package" / "def456" / "include" / "other.h").exists())

    def test_makefile_local_recover_reports_uncopied_conan_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "PROJECT_Archive_CAST"
            output = base / "recovered"
            put(root / "App" / "build" / "conaninfo.txt", "[full_requires]\n    dep/1.0:abc123\n")
            put(root / "App" / "build" / "conanbuildinfo.txt", "[rootpath_dep]\nC:/cache/.conan/data/dep/1.0/_/_/package/abc123\n")
            code = makefile_local_recover.main([
                "--root", str(root),
                "--output", str(output),
            ])
            self.assertEqual(2, code)
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertEqual(1, report["not_copied_files"])
            diagnostics = json.loads((output / "copy-diagnostics.json").read_text())
            self.assertEqual(1, diagnostics["not_copied_count"])
            self.assertEqual("conan_headers", diagnostics["not_copied"][0]["category"])
            self.assertEqual(".conan/data/dep/1.0/_/_/package/abc123", diagnostics["not_copied"][0]["source"])
            self.assertFalse(Path(diagnostics["not_copied"][0]["source"]).is_absolute())
            self.assertFalse(Path(diagnostics["not_copied"][0]["destination"]).is_absolute())
            self.assertEqual("delivered .conan/data directory not found", diagnostics["not_copied"][0]["reason"])

    def test_makefile_local_recover_deduplicates_conan_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "PROJECT_Archive_CAST"
            output = base / "recovered"
            put(root / ".conan" / "data" / "dep" / "1.0" / "_" / "_" / "package" / "abc123" / "include" / "dep.h", "#pragma once\n")
            for app_name in ("AppA", "AppB"):
                put(root / app_name / "src" / f"{app_name}.c", "int f(void){return 0;}\n")
                put(root / app_name / "build" / "conaninfo.txt", "[full_requires]\n    dep/1.0:abc123\n    missing/1.0:def456\n")
                put(root / app_name / "build" / "conanbuildinfo.txt", "[rootpath_dep]\nC:/cache/.conan/data/dep/1.0/_/_/package/abc123\n")
            code = makefile_local_recover.main(["--root", str(root), "--output", str(output)])
            self.assertEqual(2, code)
            packages = json.loads((output / "conan" / "packages.json").read_text())["packages"]
            self.assertEqual(1, len(packages))
            self.assertEqual("dep/1.0", packages[0]["reference"])
            self.assertTrue((output / "conan" / "conan-graph.json").is_file())
            self.assertTrue((output / "identity" / "BUILD_IDENTITY.json").is_file())
            diagnostics = json.loads((output / "copy-diagnostics.json").read_text())
            self.assertEqual(0, diagnostics["not_copied_count"])

    def test_makefile_local_recover_copies_root_compiler_probes(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "PROJECT_Archive_CAST"
            output = base / "recovered"
            put(root / "APP_ALPHA" / "build" / "CMakeFiles" / "APP_ALPHA.dir" / "build.make", "")
            put(root / "APP_ALPHA" / "src" / "App.c", "")
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
            root = base / "PROJECT_Archive_CAST"
            output = base / "recovered"
            put(root / "APP_BETA" / "build" / "CMakeFiles" / "APP_BETA.dir" / "build.make", "")
            put(root / "APP_BETA" / "build" / "CMakeFiles" / "APP_BETA.dir" / "flags.make", "")
            put(root / "APP_BETA" / "src" / "APP_BETA.c", "")
            put(root / "APP_GAMMA" / "build" / "CMakeFiles" / "APP_GAMMA.dir" / "build.make", "")
            put(root / "APP_GAMMA" / "build" / "CMakeFiles" / "APP_GAMMA.dir" / "src" / "APP_GAMMA.c.o.d", "")
            put(root / "APP_GAMMA" / "src" / "APP_GAMMA.c", "")
            put(root / ".conan" / "data" / "Core" / "70.0.0" / "_" / "_" / "package" / "abc" / "include" / "core.h", "")
            code = makefile_local_recover.main([
                "--root", str(root),
                "--output", str(output),
            ])
            self.assertEqual(2, code)
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertEqual(["APP_BETA", "APP_GAMMA"], report["applications_detected"])
            self.assertEqual(2, report["applications_count"])
            self.assertEqual(2, report["applications"]["APP_BETA"]["build_files"])
            self.assertEqual(2, report["applications"]["APP_GAMMA"]["build_files"])

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

