# Desired intent: Measured workflow improvement

Status: engineering direction and acceptance obligations, not a new implementation
claim, grant of authority, or replacement for existing versioned contracts.

## System intent and evidence boundary

Runspindle is intended to turn an authorized one-off task into a repeatable,
maintained workflow by composing scoped access, managed native/browser sessions,
explicit human takeover, and evaluated workflow candidates. The useful capability
is the composition: cross-application execution can retain identity and evidence
across interruptions, while approved examples can inform later DSPy experiments.
This is a design objective, not evidence of emergent intelligence, autonomous
self-improvement, deployed integration, or measured gains.

A workflow revision must identify its inputs, permitted effects, target bindings,
exception policy, acceptance criteria and resource budget. Repetition does not
create a recurring schedule or expand permission. Human intervention is first an
execution event; only a separately reviewed, consented and sanitized example may
enter a dataset. A successful evaluation is evidence for review, not authority to
promote, serve, spend, publish or act in another account.

## Component responsibility

Refinery should convert explicitly approved execution outcomes and human
corrections into reproducible experiments over recurring workflow families.
DSPy is the intended program/prompt optimization mechanism, not a promise that
more traces automatically improve performance. The unit of comparison is a locked
workflow candidate with stable interfaces and bounded effects, not an attractive
single completion or an unversioned prompt edited in production.

## Interfaces and acceptance obligations

- Separate observed events, proposed examples, approved examples, locked datasets,
  experiment results, promotion approval and served candidates. None implies the
  next. Human takeover may expose secrets or incidental data and must not become
  a dataset row without consent, minimization and provenance review.
- Lock exact dataset bytes, exclusions, family-aware splits, baseline/candidate
  bytes, metric/version, optimizer/provider configuration and thresholds. Keep
  related runs and revisions in the same partition to avoid leakage. Do not tune
  on held-out scores or silently replace a failed split after seeing results.
- Evaluate baseline and candidate on the same held-out cases, including safe
  refusal, human exceptions, uncertain effects and out-of-scope requests. Report
  sample counts, failures, uncertainty and distribution limitations; small
  synthetic samples cannot substantiate general productivity claims.
- Keep safety/authority violations as hard gates, not rewards traded against task
  success. Measure accepted task quality, intervention burden, latency and total
  resource cost under declared conditions; do not invent benchmark gains.
- Enforce one cumulative budget across optimization, baseline, candidate, scoring
  and retries. Reserve before calls; unknown usage, safety or cost blocks a pass.
  Permission to use private examples with an external provider is separate from
  permission to run an offline synthetic evaluation.
- Promotion must name the exact candidate/configuration digest and approval scope.
  Preserve a verified baseline and rollback path. A score cannot alter credentials,
  policy, tool grants, deployment configuration or recurring execution schedules.

## Present scope

Process-local lineage and scripted offline evaluation are implemented in bounded
form; real DSPy/provider execution, durable registry, trusted feedback ingestion,
promotion and candidate serving are not. Current byte-equality synthetic scoring
is not model-quality evidence. See [offline evaluation](offline-evaluation.md) and
[implementation evidence](../docs/IMPLEMENTATION_EVIDENCE.md).
