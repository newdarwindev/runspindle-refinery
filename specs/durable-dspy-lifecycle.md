# Durable DSPy lifecycle and family holdout seals

Partial issue #16 implementation with [actual native framework/restart evidence](dspy-native-qualification.md)
and [two-process ownership evidence](dspy-native-ownership-qualification.md). The
source-only SQLite tests still use handwritten result fixtures and do not themselves
prove framework execution or model-quality improvement.

## Storage and ownership

`refinery.durable_dspy.Session` composes the existing genuine DSPy adapter with the
existing `Store`, immutable objects, experiments, attempts and `SQLiteBudget`.
It adds no table, schema migration, service, background job, provider or dependency.
The explicit JSON codec admits the existing `Compiled` result type; it does not
unpickle or execute persisted code.

A fresh compilation transaction enrolls family roles, initializes or checks the
shared ledger, and claims one domain-separated identity atomically. The binding
fixes the lineage snapshot, dataset/split, optimizer/provider configuration and
budget identity. Unknown ownership cannot be replayed under the same identity.

The states are deliberately distinct:
- `compiling`: reserved ownership; interruption is uncertain and never replayed.
- `compiled`: immutable candidate checkpoint committed, evaluation not complete.
- `compile_blocked`: terminal failed compilation, no candidate acceptance.
- `evaluating`: exact candidate/family seal committed before held-out dispatch;
  interruption remains uncertain and cannot be replayed.
- `complete`: immutable terminal evaluation result. Its own status is either
  `evaluated` or `blocked`; completion never means quality acceptance or promotion.

`compile()` invokes the real `BootstrapFewShot` adapter only for a new claim.
`evaluate()` invokes the fixed baseline/candidate held-out adapter only after a
committed successful compilation. Missing DSPy fails before a new claim. A
completed read revalidates current use authority, binding, candidate, receipts,
usage and stored object hashes, and returns identical bytes without provider calls
or a framework import. An interrupted partial phase is not returned as complete.

A committed `compiled` checkpoint can continue evaluation. Only its exact
settled requests and matching immutable synthetic script prefix can restore the
in-memory scripted cursor. This is history reconstruction, not provider replay.
The caller must preserve the virtual clock epoch; a fresh clock behind the
checkpoint is rejected. The same original budget identity is mandatory, unresolved
or cancelled shared usage blocks evaluation, and optimizer/provider/candidate
configuration cannot drift. Actual framework restart through that transition is
now separately qualified by the native receipt; direct SQLite transition tests
alone do not establish it.

## Store-scoped family policy

Before compilation, immutable family records lock **both** training and held-out
roles. Related source/output identities use the existing retained family mapping.
Alternate split seeds or dataset revisions cannot move an enrolled family into
another role. Enrollment rejects prior held-out requests on any participating
family, including unresolved reservations. It also rejects ambiguous legacy
requests and known training exposure when the new role would be held-out.

Before the first held-out call, one transaction seals every participating family
to the exact owner, dataset, split, configuration and candidate. A different run,
candidate or overlapping dataset cannot evaluate that cohort or resume training
on it. Seals survive failure, cancellation and interruption. There is no automatic
reset, second-choice candidate or promotion path.

The existing durable scripted selection/evaluation entrypoints honor enrollment
and seals through their shared use guard. They cannot silently use an enrolled
cohort as a bypass. These restrictions are monotonic: old unexposed data is not
silently declared safe if legacy evidence is ambiguous or held-out was used.

This is a policy within **one component-owned Store**, not an authorization
sandbox. Storeless low-level functions, newly created stores, malicious callers
who invent unrelated identities, hostile replacement of the database and
multi-host coordination are outside this guarantee. Callers must use this wrapper
for the durable contract. Ordinary source/family lineage and use-authority checks
still apply; a score never expands authority.

Each recorded held-out prediction digest must match its final transport receipt.
The evaluator rejects parser-altered answer bytes instead of attributing an
unreceipted answer. Cached scores are recomputed from these per-example digests and
locked gold digests. Candidate seals are checked exactly at every evaluation use
boundary and on recovery. Authority callbacks are followed by fresh policy checks;
post-commit callbacks cannot turn revocation or cancellation into an outward
success, even though the earlier historical checkpoint remains committed.

## Verification

Run the source-only lifecycle tests:

```
python3 -m unittest discover -s tests -p test_durable_dspy.py -v
python3 -O -m unittest discover -s tests -p test_durable_dspy.py -v
```

Tests operate on actual disposable SQLite stores, independent processes, ownership
races, deliberate interruption, actual ledger reservations and transport calls.
They commit explicitly labeled handwritten compilation/evaluation result fixtures,
not a fake DSPy module. Tests cover cancellation, partial state, immutable
associations, usage/receipt/candidate tampering, current-authority cache reads,
partition aliases, legacy exposure and a deliberate policy-guard removal mutant.
Independent socket denial and observed provider requests identify forbidden
network/evaluation effects. Temporary files and stores are cleaned up.

The existing bounded pinned-runtime command also includes the durable adapter:

```
python3 tools/offline_dspy.py --suite optimizer --prepared local/python-deps --report local/dspy-optimizer.json
```

The reviewed adopted Python 3.14.0/Linux ARM64 lane has now run successfully; see
the exact native receipts linked above. Original `docs/durable-dspy-tests.json`
retains its source-only evidence and historical limits unchanged. Live execution,
authenticated external feedback and reflection remain separate; no production
readiness or automatic promotion follows from these offline results.
