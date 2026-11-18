# Actual-framework adverse-path qualification proposal

This source proposal extends issue #16 verification, not production behavior.
Run `python tools/offline_dspy.py --prepared <reviewed-dependencies> --suite adversarial`
only on the reviewed isolated Linux ARM64 lane. The exact adopted closure,
no-network/read-only container, resource limits, 120-second deadline and verified
cleanup are unchanged. Existing successful lifecycle receipts remain historical.

Five independent disposable SQLite stores exercise actual BootstrapFewShot:

| Scenario | New dispatches | Retained unresolved reservations | Required durable reason |
|---|---:|---:|---|
| Cancellation after reservation checkpoint | 0 | 1 | cancelled |
| Call exhaustion after a wrong trace | 1 | 0 | calls_exhausted |
| Unknown token usage | 1 | 1 | unknown_or_invalid_usage |
| Unknown cost usage | 1 | 1 | unknown_or_invalid_usage |
| Empty scripted provider failure | 1 | 1 | provider_unknown |

Transparent wrappers observe entry into the real framework `compile` method and
real provider `complete` method, then call the originals. The framework is never
replaced with a double. Network-denial instrumentation precedes framework imports.
Each case requires a blocked result with no candidate or candidate digest,
train-only dispatch, exact persisted attempt outcome and ledger charges, and a
subsequent reservation rejection without ledger changes. DSPy may wrap an adapter
exception as `optimizer_failure`; only that known wrapper or the scenario's exact
reason is permitted, while the stored stop reason must still match exactly.
No held-out evaluator is invoked and no failed candidate is promoted.

The entire command repeats twice and compares normalized evidence; it records
source, ledger and attempt hashes without temporary paths. Synthetic source tests
exercise receipt rejection, including deliberate extra dispatch and lost unresolved
reservation mutations, plus launcher isolation. They never import DSPy and cannot
establish actual qualification. Native execution and independent full-gate review
are pending until an exact source receipt is published. There is no live-provider,
concurrency, reflection optimizer, quality-gain or deployment claim.
