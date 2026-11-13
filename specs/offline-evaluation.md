# Bounded offline evaluation

`refinery.evaluation` implements a synthetic, memory-only evaluation seam against
[composition runtime.v1](https://github.com/newdarwindev/runspindle/blob/4b700269be9a4feab7b955faf0229fbdd77d8aa9/specs/runtime-v1.md).
It does not import the historical experiment helper or execute DSPy. Only the
explicit `ScriptedProvider` is accepted; no provider fallback, network client,
optimizer algorithm, approval, promotion or serving interface exists.

`evaluate(dataset, split, experiment, provider=..., budget=...)` verifies the
existing immutable lineage split and byte links, exact baseline/candidate bytes,
canonical effective configuration bytes and scripted response manifest before
any call. Related families remain in their locked partitions. Preparation stages
receive training identities only; both baseline and candidate receive precisely
the same held-out identities, in the same order. The script returns synthetic
completions; the metric is strictly byte equality, not model quality evidence.
The caller must retain the original approved lock: constructing another lock is
possible and this library is not a durable registry or authority verifier.

One injected `Budget` persists across preparation, baseline, candidate and retries.
Each call reserves known nonnegative token and cost bounds. A lock protects shared
reservations against concurrent oversubscription; runner calls are sequential.
Actual synthetic receipts settle consumption and release unused reservation.
Unknown, negative, Boolean, overflow or over-bound usage stops the ledger without
refunding its unresolved reservation. Only explicit scripted timeout receipts may
retry, at most three attempts, each charged to the same ledger. Unexpected provider
exceptions remain unknown and never retry. There is no authority to retry real
external operations.

Cancellation is checked before dispatch and after return. In-flight settled usage
remains charged. Cancellation and exhaustion stop all downstream calls. Time is
one injected virtual monotonic clock shared with the scripted provider, including
all stages/retries. A clock regression is unknown time and blocks the gate.
The deadline is exclusive: elapsed time equal to its limit blocks. Call/token/cost
ceilings are inclusive: exactly consuming their limit may complete a run, while
any additional call requiring capacity rejects. Zero limits are valid and reject
dispatch when exhausted. Empty evaluation, malformed lock/config/reply, unknown
safety/cost/usage, unsafe output, incomplete results and failed thresholds cannot
pass. Failed structural inputs raise `ValueError` before any provider call;
bounded execution failures produce `Report(status="blocked", reason=...)`.

## Native mapping

This is a native Python API, **not JSON wire conformance**. No version parser is
implemented; `Report.version` is the fixed `runspindle.runtime.v1` constant. Integers
are checked as genuine unsigned 64-bit integers. Unknown measurements use `None`.

| runtime.v1 concept | Native field / semantics |
|---|---|
| manifest_digest | `LockedSplit.sha256` / `Experiment.split_sha256`; binds `dataset_sha256`, seed and all assignments |
| baseline_digest / candidate_digest | `Experiment.baseline_sha256` / `candidate_sha256`; verified against immutable nonempty bytes |
| config_digest | `Configuration.sha256` / `Experiment.config_sha256`; canonical sorted JSON of all effective arguments |
| budget | Config `Limits(calls,tokens,time_ms,cost_microunits)` and per-call `Reservation`; supplied ledger must match |
| metric_id/version | Fixed `synthetic_exact_match.v1`; quality range 0..1 |
| provider_id / optimizer_id | Fixed `scripted.v1` / `scripted_stage.v1`, exact script SHA256 and preparation stage tuple |
| currency / cost_scale | Fixed `SYNTHETIC` / 1,000,000, never real currency spending evidence |
| min_quality | Explicit `min_score_ppm`, integer parts per million, no threshold default |
| max_quality_regression | Zero, tightened by explicit `min_improvement_ppm`; no candidate regression permitted |
| max_latency_ms / max_cost_microunits | Config global time/cost limits, stricter than a candidate-only allowance |
| require_safe | Always true; every attempted stage receipt must explicitly report safety true |
| reasons | Native `Report.reason` is one blocking reason or `None`; no provider error text copied |
| usage | Immutable key/value tuple of global calls/tokens/elapsed/cost/unresolved calls/reason |
| baseline_metrics / candidate_metrics | Native `Metrics`: quality, safety, attempted calls, summed tokens/cost/latency; missing values remain `None` |

All effective controls, including limits, per-call reservations, retry bound,
preparation stages, synthetic provider script digest and thresholds, are included
in the configuration hash. Dataset bytes, excluded reason counts, split assignments
and original feedback identities remain in the lineage lock. Reports echo those
input digests, attempted request bindings and hashed completions, not raw prompts
or gold. `ScriptedProvider.calls` is an independent observable call/order ledger.
Its request objects expose identities/hashes only; no heldout gold or approval
capability is supplied to a provider.

All state is process-local. The budget lock supports concurrent reservations in
one process, not multi-process writers. No persistence, crash recovery, durable
experiment registry, cancellation transport, real usage receipt authentication,
DSPy/provider compatibility or live acceptance is claimed. Passing a report grants
no deployment, provider transmission, spending, promotion or serving authority.
