# Bounded DSPy BootstrapFewShot component

Issue #16 remains open. The implemented optimizer now has [actual pinned offline
qualification](dspy-native-qualification.md), [adverse-path evidence](dspy-native-adversarial-qualification.md)
and [two-process ownership evidence](dspy-native-ownership-qualification.md). These
are synthetic component results, not live model quality, provider integration or promotion authority.

## Implemented path

`refinery.dspy_optimizer.optimize` invokes DSPy 3.1.0
`BootstrapFewShot.compile`, rather than ranking prewritten candidate bytes.
It compiles demonstrations for one fixed `case -> answer` Predict program.
Only locked training examples enter compilation. Exact-match acceptance is
synthetic. The framework can retry a rejected trace for 1–3 rounds, with 1–16
bootstrapped demonstrations and zero labeled demonstrations. This optimizer does
not use reflection; reflection optimizers are unsupported, not qualified here.

A supported `BaseLM` forwards to the exact existing `ScriptedProvider`. There is
no live LM, tool, network callback or provider fallback. Each framework call,
including adapter fallbacks, reserves and settles through the existing supplied
`Budget` or `SQLiteBudget`. LM copies retain the same dispatch controller, cursor,
and ledger. Unknown usage, provider failure, unsafe output, cancellation or
exhaustion blocks output; unresolved reservations are not refunded. Known provider
timeouts stop this component rather than implicitly authorizing a new effect.
The exact prior call cursor, script digest, limits, reservation and clock identity
are checked around authority callbacks and at dispatch/phase exits. Unexpected
callback effects halt without retroactively crediting them as budgeted calls;
recursive transport calls are rejected. Compilation requires a pristine cursor. Optimizer success is checked again
against cancellation and unsettled usage before a candidate can be returned.

The candidate is canonical JSON with training-only demonstrations, dataset/split,
configuration, dependency-manifest digest (through configuration), fixed signature,
and framework version. It is not pickle, executable code or a serving artifact.
Compiled demonstrations must match the admitted training inputs and gold answers.
`evaluate_locked` accepts the expected candidate SHA-256, validates all bytes and
lineage again, then evaluates the fixed empty-demo baseline and compiled demos on
the same held-out examples. It does not call the optimizer or expose held-out
labels to it. Scores do not authorize promotion.

Callers must pass the same cumulative ledger to optimization and evaluation and
supply a fresh use-authority checker, such as the existing retained-feedback guard.
Held-out continuation requires the original optimizer settings, matching
configuration and an optimization-only cursor with its ledger calls accounted for.
This rejects unrelated warmed providers and an empty/reset ledger.
This process-local API neither creates budgets nor enforces global once-only
held-out evaluation. A caller can misuse separate ledgers; preventing that across
API boundaries belongs to the remaining durable orchestration work. It does not
persist optimizer ownership, recover an interrupted optimizer, authenticate
feedback, implement provider cost measurement, or qualify concurrent use of a
single scripted cursor. SQLite reservation concurrency/durability remains the
existing ledger contract, not a new full optimizer lifecycle claim.

## Verification boundaries

`python3 -m unittest discover -s tests -p test_dspy_optimizer.py -v` tests the
actual original dispatch controller, SQLite ledger and candidate codec without
DSPy. Repeat with `python3 -O`. Network spies independently observe forbidden
calls. A deliberate allowlist mutation causes an observed held-out request, which
the independent request assertion detects. These tests never mock an optimizer
and call it real framework execution.

The real-framework command, on the already qualified isolated local arm64 lane,
is:

```
python3 tools/offline_dspy.py --suite optimizer --prepared local/python-deps --report local/dspy-optimizer.json
```

This uses the existing pinned Python 3.14/linux-arm64 image, verified dependency
bytes and network-disabled bounded container. No dependency/toolchain pin changes,
installation, service or new execution permission is implied. It exercises actual
BootstrapFewShot trace rejection and second-round LM copying, locked candidate
serialization, held-out evaluation, and shared SQLite receipts. Two runs must
produce identical candidate/report/configuration digests. The command fails, not
skips, on missing/mismatched dependencies or target. Do not run it where Docker or
runtime access has been denied.

The original source-development cloud executor lacked the reviewed runtime; its
historical `docs/dspy-bootstrap-tests.json` remains unchanged. Subsequent native
receipts linked above qualify actual compilation, locked evaluation, fresh-process
restart, bounded adverse paths and same-identity ownership. The [capacity
campaign](dspy-native-capacity-qualification.md) separately covers controlled
in-flight overlap, not simultaneous acquisition fairness. Reflection is still
unsupported; live providers remain #9. Actual-framework integrity/mutation
qualification is tracked separately and must not be inferred from source-unit tests.

Primary API references used in this implementation:
- https://github.com/stanfordnlp/dspy/blob/3.1.0/dspy/teleprompt/bootstrap.py
- https://github.com/stanfordnlp/dspy/blob/3.1.0/dspy/clients/base_lm.py

The original project adapter calls those APIs; no third-party implementation code
was copied into this repository.

A separate [durable lifecycle wrapper](durable-dspy-lifecycle.md) now adds SQLite
ownership, recovery and store-scoped family holdout seals. Those guarantees belong
to that wrapper, not these direct process-local functions. Its actual framework
execution/restart qualification is recorded in the linked native receipt. Held-out reports now retain
per-example prediction digests, and parsed answer bytes must match the final raw
completion receipt; adapter transformations or whitespace changes fail closed.
