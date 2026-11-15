# Reviewed paper in the hygiene validation contract

This integration combines hygiene PR #18 at `ee1274fabcce6b6bc7659ac667609608ccf8d8c3` with merged SRC-013 paper PR #24 at `80a87f4569f1dfef14b141e141a89f30a9aaf7cd`. The reviewed paper content commit is `bfa8f760c1d56ec1c2ce07ea49e4d37ce4c01e5b`.

The hygiene inventory declares `check-paper` as a required command and the component partition assigns it exactly once. It is no longer an indirect subprocess of scaffold validation. The new negative tests reject missing or duplicate partition assignments. The six existing paper tests retain closure and tamper checks under normal and optimized Python.

The TeX source, accepted PDF, all eight figures, license, notices, disposition, toolchain lock and original native build evidence are unchanged. Only import ordering in the CLI wrapper and whitespace in the checker changed for the existing isolated Ruff gate; the wrapper command and environment are unchanged. Its end-to-end execution remains unrun. This integration performs no additional native compilation.

The workflow, qualified runner-image gate, Tether dependency pin, pinned quality requirements and quality baseline remain unchanged. Fresh local integration receipts are separate from the original paper build evidence. Local aggregate validation is not qualified-runner CI, deployment, provider use or live acceptance. The umbrella mirror remains a separate dependency and issue #3 remains open.

The preceding mirror/open-issue statement describes the original integration
snapshot. It is superseded by the verified current status below; historical
receipts remain byte-for-byte unchanged.

Run `python3 tools/validate.py` in the prepared validation environment, then `python3 -O -m unittest discover -s tests`. The pinned Tether checkout must be supplied as `RUNSPINDLE_TETHER_SOURCE`. No installation or unpinned dependency fallback is part of these commands.

## Canonical wrapper evidence integration

Merged PR #25 main `fc79d78f3eed3423da901b1c5b623e76ce08e7c4` is
integrated into published PR #18 head
`6bf2a366a39d74cb102b220d5560e4e721c9b17b` without rewriting its ancestry.
The existing hygiene branch already had the same import-order-only wrapper.
This merge brings in the canonical supplemental receipt, README link, artifact
manifest entry and provenance link. All 19 design files match merged main.

The [separate integration receipt](../docs/paper-wrapper-hygiene-integration-evidence.json)
records fresh local checks and byte-preservation evidence. Both earlier receipts
retain their historical meaning and exact bytes. The unchanged paper checker
rejects a missing or tampered supplement and a tampered historical build receipt
under normal and optimized Python. No new paper build or wrapper execution ran.
The mandatory inventory, single paper-check partition assignment, workflows,
runner-image binding, quality baseline and Tether pin remain unchanged.

Local aggregate validation and 168 tests in each interpreter mode pass with zero
skips. Cargo is unavailable, and this component inventory contains no Rust
command. This does not qualify Rust, the runner image or native/live execution.
Independent review and qualified-image CI for this candidate remain separate
gates; this integration does not dispatch CI or claim them.

## Current mirror and issue status

[Refinery issue #3](https://github.com/newdarwindev/runspindle-refinery/issues/3)
closed as completed at `2026-11-15T00:13:33Z`, after
[root PR #59](https://github.com/newdarwindev/runspindle/pull/59) merged.
The canonical wrapper mirror subsequently merged in
[root PR #60](https://github.com/newdarwindev/runspindle/pull/60) as
`db53cd76d049d6cce94e134e5e60695d0fe6b2ad`.
[Validation run 43659354312](https://github.com/newdarwindev/runspindle/actions/runs/43659354312)
succeeded on its exact published head
`aaf5f8bd1156fb030316f6c9dea6b19a306d2dd1`.

The [current root mirror mapping](https://github.com/newdarwindev/runspindle/blob/60759ff3564ed1e61d6596edfe4a864d7989d2ab/docs/marketing/SRC-013-wrapper-mirror.json)
still pins canonical Refinery commit
`1aa0a4b0ed1494f88f9e3137ff1e2b73b7a25209`. All 17 mirrored Git blob
identities and manifest hash/canonical bindings match this candidate through
the explicit notice rename. No new mirror update is needed for this no-code
hygiene integration. The completed root check applies only to its own head,
not CI or runner-image acceptance of this candidate.
