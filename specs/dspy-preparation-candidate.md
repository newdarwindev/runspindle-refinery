# DSPy preparation candidates

Status: source-only audit contract, not a recovered original recipe, approved
replacement manifest, native preparation receipt or framework qualification.
Issue #16 remains open. The existing execution gate and all canonical dependency,
image and quality pins are unchanged.

## Why a new candidate is necessary

The original recipe in `durable-offline.md` selected Linux arm64/Python 3.14 wheels
but did not identify the installer version or preparation interpreter prefix.
Selecting target wheels does not select the interpreter path embedded in console
scripts. Those bytes and their RECORD entries belong in the strict inventory.
Do not normalize scripts, regenerate RECORDs to guessed values, ignore differences
or overwrite the canonical manifest merely to pass execution admission. An
original byte-identical artifact with trustworthy provenance remains preferable.

## Proposed deterministic preparation contract

A separately authorized preparation worker must use an empty disposable native
Linux arm64 environment with the exact existing `offline/toolchain.json` image,
Python 3.14.0 and `/usr/local/bin/python3.14`. Cross-platform wheel selection alone
is not evidence of native preparation. Capture the actual image/platform,
interpreter version/path, installer version and command outcome in a separate
execution receipt. The source-only auditor cannot verify those observations.

Before execution, retain a reviewed official uv arm64 GNU release archive, its
release checksum file, and its extracted binary. Record the exact release version,
URLs and SHA256 values. No uv version or digest is guessed or approved by this
change. Unknown source provenance blocks preparation approval. Reviewers must
verify the checksum source through an authenticated official release channel;
local checksum agreement and a URL string alone do not authenticate the publisher.
The auditor checks archive/checksum/binary consistency without executing uv.

Use only an explicitly enumerated flat wheelhouse from the existing hashed lock.
Acquire and review artifacts separately. No source distributions or source builds.
Mount the repository at `/work`, wheelhouse at `/wheels`, verified uv binary at
`/tools/uv`, and a fresh empty output at `/deps`. Disable network, use umask 022,
working directory `/work` and only the environment in the `ENVIRONMENT` constant
of `tools/dspy_preparation_candidate.py`. Run exactly its `ARGV` constant. It
specifies no config, no index, offline wheels, no Python downloads, no cache,
no bytecode and an explicit preparation interpreter. Do not run these steps as
part of the source auditor or ordinary tests. Installation requires its own
approved execution environment and reviewed inputs.

The recipe JSON has exactly these fields:

- `schema_version`: 1; `image`: existing pinned image; `platform`: `linux/arm64`.
- `python_version`: `Python 3.14.0`; `interpreter`: `/usr/local/bin/python3.14`.
- `argv` and `environment`: exact constants above; `cwd`: `/work`;
  `umask`: `022`; `network`: `none`.
- `dependency_lock_sha256` and `toolchain_sha256`: exact existing file hashes.
- `uv`: `version`, official release `url`, `archive_sha256`, `binary_sha256`,
  official asset `checksum_url` and `checksum_sha256`.
- `wheels`: object mapping every selected wheel filename to its SHA256.

Use retained filenames `uv.tar.gz`, `uv.tar.gz.sha256`, and `uv` in the artifact
directory. This contract records bytes, not authorization or source verification.
No usable example with invented production hashes is provided.

## Audit and independent comparison

Run `python3 -m tools.dspy_preparation_candidate --prepared local/python-deps
--recipe local/preparation.json --uv-artifacts local/uv-artifacts
--wheels local/wheels > local/candidate.json` only after separate preparation.
The command performs no installation, network access, dependency import, tool
execution or modification of the input tree. JSON output is deliberately wrapped
as an unreviewed candidate and cannot replace `offline/dependencies.json` directly.
Store candidates under ignored `local/`, never redirect into canonical files.

The auditor reads input files once through descriptor-relative no-follow opens
on Linux/POSIX. It bounds recipes to 1 MiB, each input tree/archive to 1 GiB and
30,000 entries, and directory depth to 64. These are source audit resource caps,
not production capacity claims. It rejects symlinks/special files, bytecode, missing/unrecorded files,
RECORD duplicates and escaping paths, wrong RECORD hashes/sizes, and console
scripts whose exact first line differs from `#!/usr/local/bin/python3.14`.
It retains the full existing package/license/file inventory and reports canonical
byte equality. Wheel archives must have valid RECORD coverage and exactly one
matching name/version/hash per locked requirement, with no duplicate or unsafe
members. Installed distribution identities must equal the selected wheel set.
All hashes, script checks and candidate inventory derive from the same captured
bytes. It does not establish that installed payloads were produced by
the supplied installer or prove wheel platform compatibility/dependency resolution.
Those require independently observed preparation and execution.

Repeat the exact approved preparation from a second empty output, retaining both
receipts. Compare recipes and candidate manifests byte for byte, including scripts
and RECORDs. No nondeterministic field is stripped. If either differs, investigate;
do not promote. A separate reviewed change must explicitly approve any new
canonical closure, preserve license evidence, and supply actual framework/restart
qualification using the approved runtime. Passing this source-only auditor grants
no permission to alter pins, deploy, serve or close issue #16.

## Source tests

`python3 -m unittest tests/test_dspy_preparation_candidate.py -q` uses only synthetic
files, archives and mocked candidate inventory, not actual uv, wheels or DSPy. These tests demonstrate
local validation and canonical non-mutation, not original artifact recovery,
native reproducibility or framework acceptance.


The strict `tools/offline_dspy.py` inventory also rejects cache directories and
`.pyc` files before admitting a dependency tree or invoking Docker. Python's
`PYTHONDONTWRITEBYTECODE` prevents cache writes, not cached-code reads; it is not
an inventory-integrity check. Synthetic inventory tests observe zero subprocess
calls for rejected trees and detect a guard-removal mutation that restores the
old false inventory match. No cached payload is executed by these tests.

## Installer-owned lock coverage

The canonical candidate auditor requires the root `.lock` installed by uv. It
accepts only an empty, regular, single-link file with exact mode `0666`, checked
through the same no-follow descriptor used to capture its bytes. Missing locks,
directories, symbolic/hard links, special files, changed contents and other modes
fail closed. Only this root path is excluded from wheel RECORD coverage; nested
`.lock` files and all other extra files still require coverage. Generic RECORD
validation remains strict and has no exception.

The empty lock is retained in the complete byte inventory and canonical-manifest
comparison, not removed or normalized. No canonical manifest, dependency pin or
framework-qualification status is changed. Synthetic tests exercise these
boundaries and preserve the original inventory schema; they do not establish
actual ARM64 installation, DSPy execution or production qualification.
