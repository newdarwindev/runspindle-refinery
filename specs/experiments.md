# Replayable experiments and candidate promotion

Status: planned Runspindle contract informed by an existing Python DSPy prototype. No provider experiment, deployed candidate, or production acceptance is asserted.

## Ownership and inputs

Refinery owns Python DSPy evaluation and candidate preparation. Tether retains the existing Slack integration boundary. Collection, provider transmission and serving authority remain separately scoped. A feedback message or optimized program cannot expand execution permissions.

Each usable example must identify immutable source and output revisions, hashes, feedback target, provenance and completeness. Quarantine incomplete or non-replayable examples. Keep related thread/revision families in the same split. Lock held-out evaluation before optimization; never select candidates on repeated test-set feedback.

## Experiment contract

Require approved data destination and bounded provider use before an experiment. Pin DSPy, model/provider configuration, dataset revision, split manifest, metric implementation and optimizer configuration. Enforce actual cumulative call, token, time and cost budgets across optimization, reflection and evaluation. Record cancellation and budget exhaustion without leaking source text or credentials.

Evaluate baseline and candidate on the same locked cases. Report attempted and excluded counts, quality, safety, latency and measured cost, retaining unknown values when unavailable. Exact-string matching is a narrow deterministic fixture metric, not sufficient evidence of general summarization quality. Do not interpret absent safety or cost measurements as passing or zero.

## Promotion and rollback

Bind approval to verified artifact bytes and digest, evaluation evidence, thresholds and target serving environment. Authenticate the approver through an external authority; a caller-supplied Boolean or actor string is insufficient. Verify artifact integrity and the locked evaluation before promotion. Use atomic durable updates with concurrency control and append-only audit evidence. A failed load keeps the last approved serving version active.

Rollback selects a specific previously approved artifact, verifies availability and compatibility, and preserves historical output attribution. Validate empty history, null predecessors, repeated rollback and concurrent promotion explicitly. A registry change alone is not proof that a serving process loaded the artifact.

## Existing prototype and gaps

The reviewed experiment helper filters on a replayable flag and reports unknown safety and cost. That flag alone does not verify lineage or split isolation. Its optimization input truncation and optimizer-specific options do not enforce one global provider-call ceiling. The promotion helper checks a digest-shaped string, declared metrics and an approval-shaped record; it does not verify artifact bytes or independently authenticate approval. Registry writes are direct local JSON writes without atomicity or concurrency protection. Treat these helpers as design evidence, not production promotion controls.

## Required acceptance

Run synthetic fixtures for empty evaluation, missing/invalid metrics, split leakage, bounded call exhaustion, malformed/mismatched digests, altered artifact bytes, unauthorized approval, concurrent promotion, interrupted writes, failed serving load and rollback edge cases. Record exact revisions and outcomes. Provider-backed tests need separately authorized transmission and spending; publishing the specification does not authorize them.

## Source provenance

Adapted from owner-selected source material identified by the opaque source IDs in `../provenance.json`. Exact original locations, revisions and hashes are retained in a private provenance record. This specification does not import executable dependencies or change upstream licenses.
