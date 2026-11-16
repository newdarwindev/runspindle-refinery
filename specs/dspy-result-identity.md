# DSPy lifecycle result identity at commit boundaries

Partial #16 integrity work on the existing `Session` lifecycle. No optimizer,
provider, model-quality, live-serving or production-authority qualification is
claimed. Tests use actual SQLite and the existing synthetic dispatch transport,
with explicitly handwritten compilation/evaluation fixtures, not a fake DSPy.

## Failure and invariant

A checkpoint callback could mutate the caller's `Compiled` object after its record
had been serialized. The old commit path then returned different candidate bytes
than the immutable record, because later checks covered cursor, usage and ownership
but not full result equality. An evaluation callback changing scores before commit
was rejected only after `complete` had already been committed.

`commit_compiled` and `commit_evaluated` now capture canonical result bytes at entry,
before authority callbacks. `verify_result_identity` compares those captured bytes
with both the current caller result and the exact persisted record digest. Checks
run after insertion, after precommit checkpoint/authority callbacks and before
outward return following postcommit callbacks. Compiled semantics are revalidated
against retained receipts at those final boundaries; evaluation validation also
runs inside the transaction after callbacks, not only after commit.

A precommit mutation rolls back the proposed checkpoint and lifecycle completion.
Previously settled requests and consumed budget remain retained. The interrupted
`compiling`/`evaluating` claim cannot be automatically replayed. A postcommit
mutation denies the invocation without rewriting the earlier historical record;
fresh recovery returns only those original committed bytes, with fresh existing
use-authority checks and no provider replay or framework import.

No codec, schema, migration, dependency pin, workflow, authority scope or holdout
policy changes. These synchronous observations assume the existing component-owned
store and trusted callbacks. They do not establish protection against arbitrary
in-process/hostile-store replacement or atomic external revocation. Cancellation,
uncertain phase ownership, and separately approved actual framework qualification
retain their existing boundaries.

## Verification

`tests/test_dspy_result_identity.py` covers pre/postcommit candidate and nested
score mutations, mutation in entry and post-checkpoint authority callbacks, actual
rollback/history readback, retained settled usage, fresh-process no-replay recovery,
zero network calls and a deliberate identity-guard removal mutant. The existing
`test_durable_dspy.py` suite separately covers receipts, seals and phase ownership.

Run the new tests normally and with `python3 -O -m unittest discover -s tests -p
test_dspy_result_identity.py -v`. Full-suite tests require the unchanged prepared
Tether source pin through `RUNSPINDLE_TETHER_SOURCE`. Exact evidence and unavailable
checks are recorded in `docs/dspy-result-identity-evidence.json`.
