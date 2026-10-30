import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import makefile_sdk_recover as recovery


def put(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class MakefileSdkRecoveryTests(unittest.TestCase):
    def fixture(self, base):
        root = base / "Application_Archive_CAST"
        profile = {
            "schema_version": 1, "compiler": "aarch64-vendor-linux-gcc", "architecture": "aarch64",
            "application_roots": ["src", "include"], "source_globs": ["src/*.c"],
            "flags": ["-mbranch-protection=standard", "-O2", "-Wall"],
            "defines": ["_FORTIFY_SOURCE=2", "RTI_LINUX"],
            "includes": [
                {"path": "include", "kind": "I"},
                {"path": "DDS/include", "dependency": "dds", "kind": "I", "subdirectories": [".", "ndds"]},
            ],
            "sysroot": {"path": "sdk/sysroots/aarch64-linux", "include_subdirectories": ["usr/include"]},
            "evidence_files": ["Makefile", "build_tools/cflags.mk", "sdk/environment-setup"],
        }
        for relative, text in {
            "src/main.c": '#include "local.h"\nint main(void){return 0;}\n',
            "src/nested/other.c": "int other(void){return 1;}\n",
            "include/local.h": "#pragma once\n",
            "DDS/include/ndds/dds.h": "#pragma once\n",
            "sdk/sysroots/aarch64-linux/usr/include/stddef.h": "typedef long size_t;\n",
            "sdk/sysroots/aarch64-linux/usr/include/no_extension": "/* header */\n",
            "sdk/sysroots/aarch64-linux/usr/include/lib.so": "binary-not-a-header",
            "build/old.d": "old.o: /old/absolute/main.c\n",
            "Makefile": "all:\n\ttouch MUST_NOT_RUN\n",
            "build_tools/cflags.mk": "CFLAGS=-O2\n",
            "sdk/environment-setup": "export CC='aarch64-vendor-linux-gcc'\ntouch MUST_NOT_RUN\n",
        }.items():
            put(root, relative, text)
        profile_path = base / "profile.json"
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        return root, profile_path, base / "recovered"

    def run_recovery(self, root, profile, output):
        with contextlib.redirect_stdout(io.StringIO()):
            return recovery.main(["--root", str(root), "--profile", str(profile), "--output", str(output)])

    def test_exploratory_zip_preserves_sources_headers_paths_and_reservations(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, profile, output = self.fixture(base)
            self.assertEqual(0, self.run_recovery(root, profile, output))
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertEqual("EXPLORATORY", report["status"])
            self.assertFalse(report["strict_qualified"])
            self.assertEqual(1, report["compile_commands"])
            self.assertEqual(["source/manual/src/nested/other.c"], report["sources_without_reconstructed_command"])
            self.assertTrue(any("environment-setup.d" in value for value in report["reservations"]))
            commands = json.loads((output / "compilation/compile_commands.json").read_text())
            self.assertEqual(".", commands[0]["directory"])
            self.assertEqual("source/manual/src/main.c", commands[0]["file"])
            self.assertEqual("source/manual/src/main.c", commands[0]["arguments"][-1])
            self.assertIn("--sysroot=dependencies/sdk", commands[0]["arguments"])
            self.assertEqual(["-I", "source/manual/include", "-I", "dependencies/dds", "-I", "dependencies/dds/ndds"],
                             commands[0]["arguments"][-8:-2])
            self.assertNotIn(str(root), json.dumps(commands))
            au = json.loads((output / "cast-config/single-analysis-unit.json").read_text())
            self.assertEqual("", au["force_include"])
            self.assertEqual("disabled_unverified_compiler_context", au["force_include_status"])
            self.assertFalse((root / "MUST_NOT_RUN").exists())
            self.assertFalse((base / "MUST_NOT_RUN").exists())
            with zipfile.ZipFile(output.with_suffix(".zip")) as archive:
                self.assertIn("source/manual/src/main.c", archive.namelist())
                self.assertIn("dependencies/dds/ndds/dds.h", archive.namelist())
                self.assertIn("dependencies/sdk/usr/include/no_extension", archive.namelist())
                self.assertNotIn("dependencies/sdk/usr/include/lib.so", archive.namelist())
                self.assertIn("cast-config/MODULE_CREATION_HELP.md", archive.namelist())
                self.assertIn("CAST_ANALYSIS_PLAN.md", archive.namelist())
            coverage = json.loads((output / "copy-coverage.json").read_text())
            self.assertEqual(len([p for p in root.rglob("*") if p.is_file()]), coverage["root_files"])
            self.assertEqual(coverage["root_files"], coverage["copied_from_root_files"] + coverage["not_copied_files"])
            mapping = json.loads((output / "copy-mapping.json").read_text())["copied"]
            self.assertTrue(all((root / row["source"]).is_file() and (output / row["output"]).is_file() for row in mapping))
            diagnostics = json.loads((output / "copy-diagnostics.json").read_text())["not_copied"]
            self.assertIn({"source": "build/old.d", "reason": "dependency_trace_not_required_for_analysis"}, diagnostics)

    def test_missing_sdk_preserves_diagnostics_without_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            root, profile, output = self.fixture(Path(temp))
            data = json.loads(profile.read_text())
            data["sysroot"]["path"] = "missing-sdk"
            profile.write_text(json.dumps(data))
            self.assertEqual(2, self.run_recovery(root, profile, output))
            report = json.loads((output / "recovery-report.json").read_text())
            self.assertIn("MISSING-INCLUDE-TREE", [issue["code"] for issue in report["issues"]])
            self.assertFalse(output.with_suffix(".zip").exists())

    def test_no_selected_sources_is_not_a_successful_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root, profile, output = self.fixture(Path(temp))
            data = json.loads(profile.read_text())
            data["source_globs"] = ["absent/*.c"]
            profile.write_text(json.dumps(data))
            self.assertEqual(2, self.run_recovery(root, profile, output))
            self.assertFalse(output.with_suffix(".zip").exists())

    def test_rejects_unsafe_profile_paths_and_flags_before_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root, profile, output = self.fixture(Path(temp))
            initial = json.loads(profile.read_text())
            for bad_path in ("../outside", "C:/external/include", "/external/include", "..\\outside"):
                data = json.loads(json.dumps(initial))
                data["includes"][0]["path"] = bad_path
                profile.write_text(json.dumps(data))
                with self.assertRaises(SystemExit):
                    self.run_recovery(root, profile, output)
                self.assertFalse(output.exists())
            for flag in ("-I/old/path", "--sysroot=/old/path", "@args.rsp", "-DOTHER=1"):
                data = json.loads(json.dumps(initial))
                data["flags"].append(flag)
                profile.write_text(json.dumps(data))
                with self.assertRaises(SystemExit):
                    self.run_recovery(root, profile, output)
                self.assertFalse(output.exists())

    def test_rejects_overlapping_output_and_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root, profile, output = self.fixture(Path(temp))
            for destination in (root / "recovered", root.parent, root):
                with self.assertRaises(SystemExit):
                    self.run_recovery(root, profile, destination)
            self.assertEqual(0, self.run_recovery(root, profile, output))
            with self.assertRaises(SystemExit):
                self.run_recovery(root, profile, output)


if __name__ == "__main__":
    unittest.main()
