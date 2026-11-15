# Evaluation admission hygiene integration

This integrates the reviewed runtime from Refinery PR28 and the guidance from
PR27 into the existing PR18 hygiene candidate. Runtime main is
`d0a28cb969624f37c24aa8318d05be6c7599e1d5`, containing the published runtime head
`e2bd1e7ce7bf252245a2b01d497dc33dab485478` and the exact approved AGENTS.md.
This is component source integration, not qualification of PR18's pinned image.

## Ancestry and conflict resolution

Local preparation began from published PR18 head
`f98192e0692667efd4aa53d9082626fcc6d959d9` while PR28's required validation was
queued. The first local merge has ordered parents PR18's head and PR28's published
head. After the existing PR28 validation succeeded and GitHub normally merged it,
the actual main merge was reconciled into the local candidate. That second merge
changed ancestry only: both source trees are
`b1f1a81f191bd8045a855bc30ddc7f68e217bcd7`. Local commit IDs in the receipt are
review identities, not promised public refs; subsequent publication must verify
the corresponding trees and ordered public parent chain.

Conflicts were limited to `refinery/evaluation.py` and
`refinery/durable_evaluation.py`, where runtime admission changes overlapped
existing hygiene formatting/import normalization and typing-only annotations,
identity casts and the budget Protocol. The resolution retains both. Ruff formats
the evaluator's new runtime code. No additional runtime algorithm or new type
workaround was needed to satisfy the unchanged static gate.

All 13 runtime modules have the same normalized executable AST as published
PR28 after removing imports, annotations, the typing-only `_UseBudget` Protocol
and runtime-identity `cast` calls. The import and typing differences are separately
reviewed; AST normalization is not a substitute for that review. Dataset/use
authority, immutable bindings, cursor/configuration guards, receipt/safety
observations, cancellation, thresholds, ownership and accounting remain unchanged.

## Evidence and preservation

The [integration receipt](../docs/evaluation-admission-hygiene-integration-evidence.json)
records current hashes, ancestry and local results. The original runtime receipt
and all older paper/retained-feedback receipts retain their historical hashes;
they do not claim to describe subsequently formatted source.

Full local mandatory validation and the optimized suite exercise 197 tests with
zero skips. The existing evaluator/durable mutation suites and the two admission
mutations remain detected. Scoped strict mypy passes the five listed typed files.
The unchanged repository quality baseline passes with zero new diagnostics and
659 surviving historical diagnostics. This is not an unrestricted full-source
strict-type pass or a reset of existing debt.

Workflows, image bindings, mandatory inventories, quality baseline, dependency
pins, provenance, accepted design artifacts and all historical evidence remain
unchanged from PR18. AGENTS.md is byte-identical to merged runtime main, including
PR27's guidance. No baseline refresh, exception, exclusion or suppression is added.

Use already prepared pinned tools on PATH and the exact Tether source:

- `RUNSPINDLE_TETHER_SOURCE=/path/to/tether python3 tools/validate.py`
- `RUNSPINDLE_TETHER_SOURCE=/path/to/tether python3 -O -m unittest discover -s tests -v`
- `python3 tools/static_quality.py`

PR28's successful validation run `43697398155` qualifies only its own head, not
this hygiene integration or PR18's pinned image. No image-CI rerun, manual
dispatch, image/fleet change, provider execution, deployment, native build,
installation, credential change, persistent job or live acceptance is claimed.
