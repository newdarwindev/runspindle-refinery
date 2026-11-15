# Durable evaluation provider admission

Partial Refinery #16 correctness work over the existing SQLite evaluation path.
No new optimizer, provider, serving adapter, schema, deployment or authority is
introduced. Scripted scores remain synthetic exact-match observations.

## Admission and ownership

The existing evaluator's provider/configuration validation is shared with
`durable_evaluation.run`. A new run validates the exact Experiment and supported
ScriptedProvider relationship, configuration digest, provider identity/script
digest, bounded integer limits/reservations, bounded immutable script, list
cursor and finite bounded virtual clock before changing a ledger or claim.
Malformed usage receipts remain runtime failures, not guessed or prepaid usage.

Use authority still defaults to deny and runs before execution admission. The
new-run check occurs after those callbacks, under the existing Store transaction.
A consumed provider cannot become a new durable evaluation under a binding that
does not describe its starting cursor. Valid new ownership always starts at zero.
The existing SQLiteBudget constructor joins that transaction, preserving its
standalone transaction behavior elsewhere. New ledger creation and experiment
claim commit together; an incompatible shared ledger or failed claim rolls both
back without resetting existing counters or reservations.

Completed-result recovery retains exact report identity, type, version, lineage,
configuration and outcome checks plus current retained-use authority. It does not
need an unused provider or reconstruct/open a ledger. Conflicting identities and
interrupted ownership also reject before ledger mutation. Neither zero calls nor
a failed preflight grants permission to replay an already owned uncertain run.

## Dispatch and completion

The durable wrapper pins the evaluator's initial provider cursor to zero. Each
existing pre-reservation and pre-dispatch guard revalidates configuration and the
expected cursor after its use-authority callback. The expected cursor advances
only with recorded attempts. Provider consistency is checked after a known
receipt settles, before safety/timeout exits, and again before quality thresholds
and the existing atomic completion snapshot, without another authority callback. Unexpected
cursor/configuration changes halt the cumulative ledger. A pending reservation
remains unresolved and charged; settled usage is not refunded.

This catches callback-driven prefix consumption after ownership, after
reservation, between calls, and during the final completion, including failing
quality, unsafe, unknown-safety and timeout outcomes. Independent provider
call observations establish that the runner does not dispatch further calls when
drift is observed. The component cannot prevent an injected trusted callback from
calling the provider itself; it refuses to certify those extra calls as a passing,
fully budgeted evaluation. No hostile-process or concurrent-memory isolation is
claimed.

The process-local evaluator still accepts a previously consumed provider by
default, preserving the intentional optimize-then-evaluate shared-budget seam.
Only the durable wrapper supplies the zero-start requirement. Thresholds, retries,
held-out partitions and cancellation after the final atomic snapshot are unchanged.

## Verification and remaining limits

`tests/test_evaluation_admission.py` uses actual disposable SQLite stores,
subprocess restart, concurrent connections, independent SQL/provider observations
and socket denial. It includes deliberate admission-removal and cursor-guard
removal mutations that respectively expose poisoned claims and a false passing
score. Existing evaluation, optimization, retained-use and serving tests remain
part of the full suite. See the [component receipt](../docs/evaluation-provider-admission-evidence.json).

With the already prepared Tether source at the pinned commit, run:

- `RUNSPINDLE_TETHER_SOURCE=/path/to/tether python3 tools/validate.py`
- `RUNSPINDLE_TETHER_SOURCE=/path/to/tether python3 -O -m unittest discover -s tests -v`
- `python3 tools/check_evaluation_mutations.py`
- `python3 tools/check_durable_mutations.py`

These commands do not prepare dependencies or contact providers. Live consent,
actual DSPy optimization, real provider spending, production serving, native
qualification and deployment remain separate unqualified work. Issue #16 remains
open. Existing historical evidence files are not rewritten by this change.
