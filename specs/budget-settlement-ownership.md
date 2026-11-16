# Durable reservation settlement ownership

Partial #16 and parent #5. This extends the existing `SQLiteBudget` used by
scripted evaluation and DSPy lifecycle adapters, without a new optimizer, provider,
schema or external service.

Previously, an experiment sharing a ledger could settle another experiment's
reservation ticket. Its attempt update affected zero rows, but the shared pending
reservation was removed. In the baseline reproduction, experiment A reserved all
10 tokens/cost units, B settled A's ticket with zero usage, A's attempt remained
unknown with no receipt, and B reserved all capacity again. An ownerless wrapper
could also alias experiment-owned reservations.

New reservations atomically add a compact ownership digest to the ledger's pending
ownership map. The domain-separated digest binds budget identity, ticket,
experiment (including explicit `None` for ownerless use), exact reservation and,
for experiment-owned calls, the canonical retained request bytes. This preserves
the existing 4096 pending-ticket payload capacity without introducing an immutable
object kind whose 1000-record quota would silently lower that capacity.

Before any settlement mutation, the wrapper verifies the exact ownership digest
against the pending reservation and caller's ledger/experiment. Experiment-owned
settlement also requires the retained unknown attempt and an `Attempt` receipt
with its exact request and matching usage arguments. Wrong owner, wrong ledger,
missing/changed request, missing proof or mismatched receipt raises before any
ledger or attempt writes. Successful settlement removes only that pending proof.
Unknown/invalid usage retains its reservation and proof; cancellation and stop
remain shared-ledger operations rather than per-experiment permissions.

Clock callbacks cannot silently replace the ledger, attempt, caller binding or
supplied settlement evidence during an operation: these are reread before writes
in the same SQLite transaction. A detected callback mutation rolls back the local
transaction, including its own SQL changes. Callbacks must not independently
commit, rollback or close the trusted connection.

Legacy payloads without ownership maps can still be observed, canceled and used
for new reservations within their remaining capacity. Their old pending tickets
cannot be settled without proof: they remain charged, with no inferred ownership,
implicit repair, refund or migration. Ordinary ledger serialization may include
an empty ownership map, but never fabricates proofs for existing pending tickets.
The existing global payload and attempt limits remain unchanged. Legacy settled
state remains compatible. Concurrent old-code writers are unsupported: they do
not preserve the ownership map and can erase pending proofs. Ownerless use is
deliberately shared among wrappers carrying the explicit None owner.

This is transactional ledger-integrity checking, not authenticated caller identity
or immutable ownership history. The host still controls experiment labels and
stores. It does not defend against arbitrary hostile edits that forge consistent
ledger proofs, nor prove billing accuracy, provider usage, power-loss durability,
live DSPy qualification or production storage acceptance.

Tests use real disposable SQLite ledgers, synthetic requests/receipts and no
provider. They independently inspect complete logical dumps on denial, ownerless
and cross-budget collisions, changed receipt/request, legacy pending behavior,
callback mutation, racing owners, unknown usage/cancellation, and fresh-process
foreign denial followed by exact owner settlement. A deliberate removed-owner
check must break the negative regression. A separate serialization check retains
4096 pending proofs under the existing payload ceiling.

```
python3 -m unittest discover -s tests -p test_budget_settlement_ownership.py -v
python3 -O -m unittest discover -s tests -p test_budget_settlement_ownership.py -v
```

Full-suite reproduction requires the existing Tether pin via
`RUNSPINDLE_TETHER_SOURCE`. Results and blocked checks are recorded in
`docs/budget-settlement-ownership-evidence.json`. No deployment, migration, access
change, live provider, paid service, persistent job, backup or package installation
is part of this work. #16 remains open; this partial work does not change parent issue status or
establish broader completion.
