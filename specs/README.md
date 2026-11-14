# refinery specification

Status: partial process-local lineage candidate; upstream integration and live acceptance not verified.

## Responsibility

Python DSPy experiments, feedback lineage and approved candidate serving.

## Shared identifiers

Correlate task, intent revision, selected host, native session, browser instance, lease epoch, trace/span, artifact digest and Slack destination without placing credentials or raw private content in public receipts.

## Required behavior

Preserve explicit authority and unknown outcomes. Distinguish request admission, execution, validation, acceptance and delivery. Exact state transitions belong in component-specific specs added beside this file.

## Acceptance

1. Unit/contract tests with synthetic data.
2. Isolated integration tests against pinned upstream dependencies.
3. Bounded live acceptance in an authorized target.
4. Failure, restart, uncertain-effect reconciliation and rollback evidence.

Synthetic lineage component tests now run separately. Isolated integration, durable recovery, provider/serving and live acceptance remain blocked/not-run. See [implementation evidence](../docs/IMPLEMENTATION_EVIDENCE.md).

Detailed adaptation: [experiments.md](experiments.md).

The [bounded offline evaluation seam](offline-evaluation.md) now exercises actual
process-local runner code with scripted providers, locked lineage and global
budgets. This is component evidence; real DSPy/provider, durable registry and
serving acceptance remain blocked.

The [durable offline mapping](durable-offline.md) adds bounded SQLite lineage, experiments and approved disposable synthetic serving. Trusted collection, providers and live acceptance remain unimplemented/not-run.
## Engineering direction

[Desired intent](desired-intent.md) connects this component to scoped access,
managed sessions, explicit human exceptions and evaluated maintained workflows.
It adds design and verification obligations without changing runtime schemas,
existing acceptance results or operational permissions.

- [Scripted training-only selection](scripted-training-selection.md): bounded offline candidate search using the existing shared budget; no DSPy/live quality or serving claim.

- [Durable scripted training selection](durable-training-selection.md): bounded
  SQLite claims, cumulative reservations and exact committed-result recovery.
