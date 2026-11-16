# Linux x86_64 DSPy preparation candidate

Status: **UNREVIEWED candidate**, not execution authority, canonical qualification,
framework acceptance, promotion or issue #16 completion. The ARM auditor, default
execution gates, canonical dependency manifest and 76-package hashed lock are
unchanged. This profile is opt-in and source-only.

## Explicit profile

`tools/dspy_x64_preparation_candidate.py` pins uv 0.10.6 GNU x86_64 and Astral
CPython 3.14.10 build 20261110 official release URLs and archive SHA256 values.
The lock remains SHA256
`830486665f562043c93297ff9da93db9b379c13cf12eba680b320dc1c5b4b525`, including
DSPy 3.1.0. Local equality against these pins establishes byte consistency, not
publisher authentication or permission to execute. Official origin verification
and operator-observed installation receipts require independent review.

The inert `recipe_for(root, wheel_hashes)` function supplies the complete exact
recipe, including two argv arrays, clean environment, fixed interpreter prefix,
cwd and umask. Both separately prepared output directories must use the same
absolute interpreter path. No path or RECORD normalization is permitted.
The recipe explicitly says `installer_offline_flags_only_not_os_isolation`.
It does not pretend uv's offline options provide network namespace confinement.
No installation, subprocess, dependency import or network operation occurs in
the auditor. Standard-library source tests do not execute the downloaded tools.

Required task-local layout:

- `requirements.lock`, copied byte-for-byte from the canonical lock.
- `artifacts/uv.tar.gz`, `artifacts/uv.tar.gz.sha256`, `artifacts/python.tar.gz`.
- `uv-release/uv-x86_64-unknown-linux-gnu/uv` and the rest of that exact archive.
- `runtime/python/`, matching the entire pinned CPython archive.
- `wheels/`, exactly one flat wheel per locked package.
- `deps-a/` and `deps-b/`, separately installed from empty directories.
- A recipe JSON produced from the explicit root and actual wheel hashes.

After separate authorized preparation, run:

```
python3 -B -m tools.dspy_x64_preparation_candidate \
  --root /absolute/task/root --recipe /absolute/task/root/recipe.json
```

Retain output only under ignored local evidence storage, never canonical files.
Do not commit real host paths, private receipts or installed third-party bytes.

## What is checked

The auditor compares exact complete dependency bytes and modes across both
snapshots; strict package RECORD coverage, hashes and sizes; exact script shebangs;
76 matching locked distribution identities; and existing full license/notice
inventory checks. Every selected wheel is checked against its locked hash,
bounded archive paths and RECORD, filename identity and WHEEL tags. This profile
accepts non-free-threaded CPython 3.14, universal Python 3 wheels, applicable abi3
wheels and GNU x86_64 manylinux tags up to glibc 2.28. The preparation host must
separately demonstrate Linux x86_64, glibc >= 2.28 and the exact CPython identity.
ELF headers are checked for x86_64, and extracted installer/runtime bytes and
modes must exactly match the pinned archives. The auditor never executes a
binary to infer its identity.

Dot-compressed tag components in filenames and WHEEL metadata are expanded with
the same bounded grammar before comparing their exact sets. Duplicate expanded
tags, empty or malformed components, mismatched sets and profiles with no
compatible tag are rejected. This supports equivalent compressed representations
without accepting a different platform or changing the exact runtime-mode policy.

uv's observed root `.lock` is an explicitly separate installer artifact: exactly
one empty, regular, single-link, mode-0666 file. Its unchanged bytes remain in the
full candidate manifest and complete tree comparison. Only this artifact is
excluded from package RECORD coverage. It is never represented as a package file
or removed from the prepared tree. Nonempty, linked, differently located or
incorrect-mode variants, and any other unrecorded file, are rejected. Mode 0666
is observed installer output, not evidence of safe execution or storage. Any
future executor must seal or read-only mount an independently reviewed closure
and account for writable host files and parent-directory permissions. Do not
chmod or normalize the preparations to conceal their observed state.

All ordinary input trees reject symlinks, special files and dependency bytecode.
The CPython runtime alone permits the exact pinned archive's opaque relative
symlink text and original bytecode. Links are never followed. Absolute, escaping,
dangling, cyclic and directory/indirect-prefix links are rejected. Runtime link
inventory and every regular-file byte/mode must match the pinned archive.

## Bounded snapshots and limits

Descriptor-relative no-follow/nonblocking reads protect against symlink traversal
and FIFO blocking. File and directory identity, size, mode and timestamps are
checked around reads to reject observed mutations/replacements. Validation uses
the captured bytes, not subsequent path rereads. JSON files are capped at 1 MiB;
each tree/archive at 1 GiB, 30,000 nodes and 64 path separators; archive expansion
is capped before parsing. Wheel RECORD handling retains the stdlib CSV field cap.

This is not a filesystem transaction or a defense against a hostile same-UID
writer that can mutate and restore inputs between observations. Preparation
requires quiescent task-local inputs. Exact equality does not prove that two
install commands actually ran, that package payloads were generated by those
commands, or that the host identity/receipt is authentic. Independent observation
and review remain necessary. No receipt or candidate can authorize framework
execution or replace the existing canonical gate.

## Tests and remaining work

`python3 -B -m unittest tests.test_dspy_x64_preparation_candidate -q` uses generated
synthetic wheels, fake ELF bytes and small archive trees. It covers a complete
76-package synthetic candidate, exact repeatability, wrong profile/pins, runtime
and link tampering, dependency differences, unrecorded payloads, `.lock` variants,
symlink/FIFO/ancestor-link rejection, mutation/parent-swap detection, confined link
chains, cycles/traversal, archive bounds, wheel tags and duplicate JSON fields.
These tests are source-validation evidence only. Real artifact comparison, origin
review, runtime/host receipts and framework/restart qualification are distinct.

Synthetic installer/runtime files explicitly receive their declared archive modes
when fixtures are constructed. Inheriting the caller's umask would produce a
different fixture (for example, 0664 instead of the declared 0644 with umask 0002)
and reject otherwise valid synthetic candidates before the intended negative
case. This fixture setup does not chmod prepared candidate inputs or relax the
auditor's exact-mode comparison.
