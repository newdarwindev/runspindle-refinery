# Retained-feedback use eligibility

Partial issues #15/#16 implementation, using the existing component stores and
pinned Tether dependency. This is local observed-evidence validation with synthetic
use authorities, not live consent authentication, real training, provider spending,
promotion authority or cross-database atomic revocation.

## Explicit use boundary

`durable_evaluation.run` and `durable_optimization.run` now default to deny. For a
retained-feedback snapshot, supply `RetainedFeedbackUse` with the actual pinned
Tether `FeedbackStore`, an independent material verifier and a fresh `approve_use`
callback. Both callbacks must return exactly `True`. Missing, false, nonboolean or
raising callbacks deny. Stored collection/training references do not authorize
execution or cached result reuse.

Each use request binds the snapshot hash, original batch hash, collection receipt
hash, evaluation/optimization purpose, configuration hash and full run binding
hash (including caller identity and budget identity). It is not a reusable token.
The callback must decide current permission for that exact request each time.
Integrating a real consent authority and source-material authority remains open.

The reusable verifier reads the immutable snapshot and referenced collection
receipt, verifies their hashes and exact association, checks the pinned dependency,
and rereads every exact selected Tether revision. Selection order, event digest,
current active head/state, actor, judgment/correction, rule, material reference,
source/output bytes and target association must match. It reconstructs the batch's
dataset and locked split rather than allowing a receipt to authorize unrelated
examples. Conservative scope: historical batches with unavailable selections or
additional cross-batch conflict observations cannot execute through this gate.
They remain available as immutable audit records; they are never recollected or
silently re-split.

After all authority callbacks, dependency identity and all Tether evidence are
reread again. A callback that changes feedback while returning `True` cannot leave
an old selection eligible. These are bounded observations of separate databases;
changes after the final read cannot be made atomic with dispatch or result commit.
Component-owned stores/processes and injected callbacks remain trusted.

## Dispatch, recovery and accounting

The durable wrappers check before new experiment claims and budget creation,
including restarts and cached results. Existing evaluation/selection loops check
before each reservation and again immediately after reservation, before every
provider call or retry. Cancellation checks surround each dispatch use check.
Use denial halts the ledger without refunding a pending reservation or retrying.
Known receipts already settled retain their recorded usage. Interruption and
unknown-outcome ownership still prohibit automatic replay.

A fresh check runs after the existing before-result-commit checkpoint, inside the
Refinery transaction. Failure rolls back the proposed result and completion state,
not previously committed attempts or reservations. Another check runs after the
post-commit checkpoint and before returning. If revocation is observed after a
successful commit, the historical result stays immutable, but the invocation and
later cached reuse are denied. Cached results require the same fresh checks even
when no provider calls are needed.

Standalone synthetic fixtures are a separate explicit test path:
`synthetic_fixture=True` requires the existing `synthetic-consent` fixture marker,
no retained verifier, and no stored collection receipt for that batch or any of
its feedback identities. Re-batching or changing the split seed cannot relabel a
known retained selection as synthetic. A missing
retained receipt never falls back to that path. Existing direct process-local
`evaluate`/`optimize` APIs have no retained-store provenance and remain synthetic
seams, not an alternative production entrypoint or a global training-use gate.
The optional internal `check_use` hook is wired by the durable wrappers. This
marker is not authentication: provenance deliberately erased and relabeled in a
different store cannot be inferred. Trusted callers must not pass private or
retained data through standalone synthetic seams.

## Evidence

`tests/test_feedback_use.py` exercises real pinned Tether/Refinery stores with
invented data, socket denial and scripted provider request ledgers. It covers
collect-then-edit/delete/conflict/revoke; missing/false/raising authorities; absent
or substituted evidence; forged receipt, batch, snapshot and material associations;
mutating callbacks; post-reservation and between-call/retry revocation; cancellation;
pre/post-result-commit denial; successful split preservation; fresh-process denial
without authority and cached reopened-store checks. Removing only either dispatch
guard produces independently observed forbidden calls, proving the negatives are
not just self-reported statuses. Error text does not disclose callback canaries.

The [component receipt](../docs/retained-feedback-use-evidence.json) binds tested
source hashes and records the remaining limits.

Run with the prepared pinned source:

- `RUNSPINDLE_TETHER_SOURCE=/path/to/tether python3 tools/validate.py`
- `RUNSPINDLE_TETHER_SOURCE=/path/to/tether python3 -O -m unittest discover -s tests`

No dependency installation, live provider/network use, deployment, new database,
collector, schema migration, credential, access, fleet or service change is made.
