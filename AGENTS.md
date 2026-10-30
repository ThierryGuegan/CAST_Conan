# Repository Instructions

These instructions apply to the entire repository. Read the relevant implementation
and procedures before changing behavior. Preserve existing user changes and keep
edits scoped to the requested task.

## Purpose and Architecture

This project prepares portable offline deliverables for CAST Imaging from C/C++
and Python projects using Conan and, where applicable, QNX/QCC.

- The client builds the application and exports its build context.
- CAST consumes transferred deliverables offline. Do not introduce builds, Conan
  resolution, or compiler execution into the CAST collection workflow.
- Keep the standard client CI export and the modified-makefiles local workflow
  distinct. Changes to one workflow must not silently alter the other.
- The modified-makefiles workflow runs locally and requires no CI integration.
- Recovery operates on materialized directories under `--root`, including multiple
  applications. Do not require ZIP input or a text directory listing for
  `makefile_local_recover.py` or `makefile_local_audit.py`.
- Reconstructed compilation commands, Conan metadata, or compiler probes must
  retain their provenance and limitations in reports. Do not describe inferred
  data as an exact compiler observation.

## Code Map

- `client_ci_export.py`: standard client export after a build.
- `conan2_inventory.py`: exact Conan 2 inventory in the client environment.
- `makefile_local_export.py`: normalize a locally produced staging directory.
- `makefile_local_audit.py`: inspect a received directory and missing deliverables.
- `makefile_local_recover.py`: recover available sources and build context locally.
- `makefile_sdk_recover.py`: exploratory Makefile/Linux SDK packaging from a
  reviewed JSON profile; see `docs/CAST_TEAM_PROCEDURE_MAKEFILE_SDK.md`.
- `cast_offline_collector.py`: remap, validate, and package deliverables for CAST.
- `schemas/` and `examples/`: input contracts and sample data.
- `tests/test_collector.py`: automated regression tests.
- `ci/quality_gate.py`: shared local and CI validation entry point.
- `tools/release.py`: release packaging.

## CAST Imaging Configuration

The confirmed workflow uses the automatically created C/C++ Analysis Unit (AU).
Manual AU creation is currently unavailable for C/C++ and JEE in this workflow.
Universal Technology supports it. Preserve this distinction in generated guidance.

- Use `cast-config/single-analysis-unit.json` as a manual configuration reference
  for the auto-created AU; it is not an automatic Imaging import format.
- Keep `cast-config/profile-reference.json` for compilation-context diagnostics.
  Do not generate instructions to create a C/C++ AU for each compilation profile,
  or reintroduce `analysis-units.json` as an operational artifact.
- Apply the consolidated force include only when `force_include_status` is
  `enabled`. Preserve conflict diagnostics; do not silently merge incompatible
  macro or pre-include contexts.
- Analyze the full intended source scope before creating modules.
- Maintain `module-candidates.json` and `MODULE_CREATION_HELP.md` as aids for
  post-analysis module creation. Candidates require review against analyzed objects
  and application ownership.

## Paths, Copying, and Reports

- Support Windows and Linux with Python 3.9+ and the standard library.
- Use structured parsers and argument lists for subprocesses. Never use `shell=True`.
- Preserve include ordering, macro actions, response-file expansion, and Conan
  host/build context distinctions. Avoid choosing ambiguous packages by first match.
- Operational output paths must be relative to the documented output/package root.
  Handle Windows-origin paths even when the scripts run on Linux, and vice versa.
- Preserve traceability between `--root` and `--output`: report copied-file mappings
  and files not copied with explicit reasons. Explain intentional exclusions.
- Keep unresolved paths visible. Do not invent collected files or suppress strict
  validation failures to obtain a successful status.
- Local recovery does not copy `.d` files for analysis; retain their diagnostic role
  without reintroducing absolute paths into operational output.
- Do not add client export generation of `FILES.sha256`. The collector's output
  integrity manifest is a separate artifact.
- Build logs are transferred as supplied. Do not reintroduce automatic redaction
  or collector rejection based on token, password, Bearer, or private-key patterns.

## Naming and Confidentiality

- Preserve `LCCS`, `LCCS_Archive_CAST`, and business application/module names.
- Replace the user identifier `DU9105776` with `DEVUSER` in exported artifacts.
  Broader anonymization requires an explicit request.
- Do not commit received customer archives, source bundles, logs, or generated
  analysis packages as fixtures. Prefer small synthetic regression fixtures.

## Documentation and Verification

- Keep the README, relevant procedures under `docs/`, client kits, and generated
  Markdown consistent with changed behavior. Preserve the French documentation
  style and use the term "bundle de livrables".
- Use `docs/CAST_TEAM_PROCEDURE.md` for the common CAST workflow and
  `docs/CAST_TEAM_PROCEDURE_MODIFIED_MAKEFILES.md` for local-workflow specifics.
- For audits, report actionable findings first, ordered by severity, with file/line
  references and concrete impact. Distinguish passing tests from untested behavior.
- For behavioral fixes, add focused regression coverage proportionate to the risk.
- Run `python ci/quality_gate.py` for Python or input-contract changes. It checks
  Python compilation, JSON parsing, prohibited shell calls, version consistency,
  and the unittest suite. Use `python3` where required by the environment.
- For documentation-only edits, verify referenced paths and run `git diff --check`.
- If Windows temporary-directory permissions prevent tests from running, set
  `TMP` and `TEMP` to a writable temporary directory under the workspace before
  starting Python, as documented in the README.
- Report actual validation results and skipped tests. Do not claim Windows and
  Linux execution coverage when only one platform was exercised.
