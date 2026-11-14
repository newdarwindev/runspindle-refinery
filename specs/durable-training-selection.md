# Durable scripted training selection

Partial issue #5 storage/restart implementation, not issue closure. This wrapper
uses the same bounded SQLite Store and SQLiteBudget as durable evaluation. No
schema migration, live provider, serving effect or new service is introduced.

`refinery.durable_optimization.run` validates immutable inputs before writes,
then claims a domain-separated selection identity in one SQLite transaction.
The binding includes snapshot, candidate/configuration digest, provider script,
limits, retry bounds, and shared budget identity. Conflicting repeats fail.
Completed repeats return the stored exact Selection with zero provider calls;
running/interrupted repeats fail closed rather than replay uncertain effects.
Claims with failed initialization remain blocked; no automatic recovery is offered.

Attempts reserve on the existing cumulative ledger before dispatch. Unknown
interrupted reservations remain charged. Result bytes and completion state commit
atomically. A crash before result commit retains running ownership, while a crash
after commit permits identical read-only result recovery. Concurrent contenders
use separate connections and only the first claimant dispatches. Two different
identities constitute two explicitly requested searches, not deduplication across
arbitrary caller identities. The wrapper cannot enforce global one-time holdout
policy or prevent another API from using a different budget.

SQLite clock epochs must remain comparable across processes. Only component-owned
storage and process-interruption guarantees are claimed, not hostile database,
power-loss, production multi-host storage or upstream optimizer compatibility.
The provider remains synthetic and fixed-candidate selection is not DSPy tuning.
Approval, trusted collection and production serving remain separate obligations.

BDD:
- Given a committed result, a fresh process reads identical bytes without calls.
- Given interrupted ownership, restart rejects replay and keeps reservations.
- Given racing callers, only one obtains dispatch ownership.
- Given a changed binding, reject before dispatch without resetting the ledger.
- Given a deleted-claim mutation, independently observed replay calls reveal it.

Run `python3 -m unittest discover -s tests -p test_durable_optimization.py -v`
and repeat with `python3 -O`. Tests use actual disposable SQLite databases,
subprocess termination, synthetic lineage and independent socket denial. Existing
selector tests independently observe training-only requests and a holdout-leak
mutation. Full aggregate: `python3 tools/validate.py`.

Cached results are revalidated against dataset, split, script/config binding,
training identities, candidate inventory/digest, status and deterministic scores.
Malformed stored associations fail without dispatch. New ownership requires a
pristine scripted provider cursor so identical bindings cannot conceal different
starting positions. Cached reads do not require an unused cursor. This restriction
belongs only to this durable wrapper, not to process-local shared-budget APIs.
