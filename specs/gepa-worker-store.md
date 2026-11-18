# GEPA worker-owned Store context

## Confirmed failure and correction scope

[The retained native failure](../docs/gepa-worker-failure-20261118.json) records
six actual DSPy worker SQLite thread-affinity errors before provider effects.
The original executor returned failure predictions and GEPA returned the seed;
the strict no-proposal/candidate guard correctly rejected that result. None of
those runs qualifies reflection. The correction does not relax that guard or the
five-call qualification oracle, substitute an executor, or disable SQLite thread
affinity. Original diagnostic and failed-run receipts remain unchanged.

## Ownership and transactions

`Store.worker_context()` explicitly admits one foreign worker connection per
Store. It opens the existing file on the worker with SQLite's default
`check_same_thread=True`, verifies the pinned device/inode before and after open,
requires the existing WAL/schema, and preserves FULL synchronization, foreign
keys and capacity limits. In-memory Stores cannot be reopened in workers.

Within the context, that thread's `store.db` resolves to its own connection.
This covers direct authority/Session reads as well as SQLiteBudget operations.
Outside the admitted scope, foreign-thread access fails. Nested same-thread
contexts reuse that connection; they do not implicitly create nested transactions.
Existing `BEGIN IMMEDIATE`, schema validation, reservation ownership, settlement
and rollback rules are unchanged. `get()` can join only its current thread's
transaction, never another thread's `in_transaction` state.

Worker admission rejects an active owner-managed transaction or another worker.
Owner access/close rejects an active worker. Connections and cursors are internal
component handles and must not escape their owning thread/scope; this is not a
sandbox against hostile Python code, retained raw handles or adversarial file
replacement races. The database path must remain trusted and stable.

The worker rolls back unfinished transactions and closes its connection in the
same worker `finally`, clearing its thread-local route and admission slot even on
failure. An unfinished transaction on normal exit is an error, not an implicit
commit. Cleanup attempts close even if rollback fails; cleanup failure poisons
further Store use rather than claiming a clean state. Owner close still cleans up
the original owner handle. A committed reservation followed by interruption or
revocation remains charged; it is never refunded by connection cleanup.

## Adapter boundary

The genuine GEPA program/task, metric, proposer and stop callbacks enter the
component scope. A scope only opens a worker connection when needed; main-thread
callbacks retain the original owner connection. The fixed serial profile uses a
nonblocking admission lock: overlapping invocations reject and poison the run,
rather than queueing work or replacing DSPy's ThreadPoolExecutor. Nested scopes
retain the existing dispatch reentrancy guards. First terminal budget reasons
remain authoritative. The runtime identity now also hashes `refinery/storage.py`;
external dependency/image inventories are not repinned.

## Evidence and remaining acceptance

Source tests execute real SQLite transactions and stdlib worker threads, including
an authority callback reading both `Store.get()` and `store.db`. They cover scoped
access, nested contexts/transactions, owner/worker overlap, close denial, rollback,
cleanup failure, schema rejection, unknown provider usage, cancellation, callback
mutation, post-reservation revocation and interrupted charged reservations.
These are component tests, not a substitute DSPy qualification.

Independent source review, complete native repository gates and the unchanged
actual GEPA five-call/two-run qualification are required before acceptance.
Compile-only durable GEPA work remains separately reviewed; no heldout, promotion,
live-provider or additional concurrent-optimizer support is claimed here.
