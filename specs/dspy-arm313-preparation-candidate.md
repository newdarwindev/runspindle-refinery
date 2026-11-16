# Python 3.13 ARM64 preparation candidate

This separate candidate addresses the original lane's declared Python conflict:
DSPy 3.1.0 requires `gepa[dspy]==0.0.22`, whose exact locked wheel declares
`Requires-Python: >=3.10,<3.14`. The downloaded DSPy and GEPA metadata match the
canonical manifest's metadata hashes. Do not ignore this constraint or edit the
wheel metadata. The existing Python 3.14 canonical manifest and executor remain
unchanged; neither issue #1 nor actual framework qualification is complete.

## Retained pins and acquired inputs

`offline/python313/recipe.json` pins a separate official Linux ARM64 Python
3.13.13 image, `/usr/local/bin/python3.13`, official uv 0.13.8 archive/checksum/binary,
the unchanged 76-distribution hashed requirements lock, and exact compatible wheel
filenames and digests. No distribution version was changed. All 76 wheels were
acquired from official PyPI and validated as inert archives; dependency code and
installer code were not imported or executed. Official release digests and the
uv checksum agree with the retained archive; its extracted binary is ARM64 ELF.
The exact official Python image is retained locally.

Python 3.13 wheel selection changes native payloads, generated script prefixes and
installed RECORDs, so the candidate must not be presented as the original tree.
The lock's historical header still records its original Python 3.14 compilation;
retaining those pins is not evidence of Python 3.13 resolution. A separate offline
pip dry run accepted the complete target wheel set, without `--no-deps` or ignored
Python requirements. That cross-target check uses the host's marker environment;
native uv resolution remains necessary.

## Read-only audit

Run `python3 -B -m tools.dspy_arm313_preparation_candidate --uv-artifacts
local/python313/uv-artifacts --wheels local/python313/wheels` to validate exact
recipe wheel hashes, lock identities, ZIP RECORD coverage, matching filename/WHEEL
tags, ARM64 CPython 3.13 compatibility and every actual wheel's Requires-Python.
The numeric specifier parser supports only the observed range/exclusion syntax;
unknown syntax, missing declarations and duplicate declarations deny. It is not a
general dependency resolver. The shared bounded snapshot and archive validators
retain their no-follow, size, mode, path and duplicate checks.

The optional `--first` and `--second` arguments compare two distinct prepared
directories, including every byte and mode, then verify installed RECORDs,
identities and the exact generated interpreter prefix. The expected empty regular
mode-0666 uv `.lock` is retained in the full inventory, as in the existing x64
candidate contract. Its mode is an explicit proposed contract, not an observation
of uv 0.13.8 output; a different observed artifact blocks this audit and needs
review. No prepared byte, mode, RECORD or script is normalized to pass.

## Native preparation still required

The accessible Docker daemon is x86_64. No native ARM64 execution is available in
this environment. No emulator was installed, no ARM64 container was started, and
zero native preparations or actual DSPy acceptance runs were completed. A bounded
Python 3.13 x86_64 source-test container is a language compatibility check only.

On an independently admitted native ARM64 host, verify the exact image and uv
release inputs. Preserve the binary's executable mode from the official archive.
Use two empty output directories, separately mounted as `/deps` for two fresh
containers. Mount source at `/work`, wheels at `/wheels`, and the verified uv
binary at `/tools/uv`; make all input mounts read-only. Use the recipe's exact
argv, clean environment, working directory and umask, with network disabled and
bounded container resources. Retain observed host/image/Python/uv identity,
commands, outcomes and container cleanup for each independent preparation.

Run the auditor with both outputs and retain its complete candidate inventory.
Matching files alone do not prove that two native installations actually ran.
Independent review must cover those receipts, provenance, installed payloads and
any proposed canonical change. After that review, qualify actual DSPy compatibility
and lifecycle/restart cases against the separately approved runtime. This tool
cannot execute a candidate or replace the existing executor's canonical gate.
