# Scripted selection provider binding

Partial Refinery #5 correctness work over the existing offline selector. No new
provider, protocol, schema, authority, optimizer framework or serving adapter.

The process-local selector captures its original scripted configuration and
clock object, plus the starting provider cursor. After use callbacks and budget
checks finish, it revalidates before reservation and dispatch. It checks again
after known receipt settlement, before scoring or safety/timeout exits, and
around the final successful budget snapshot. Script, limits, reservation, clock
or unexpected cursor drift blocks selection and halts the shared ledger.

The initial cursor need not be empty for process-local selection. A deliberately
consumed prefix remains supported, as does selection followed by evaluation on
the same provider/budget. Concurrent searches sharing one cursor remain
unsupported. The guard detects drift at documented callback boundaries; it
cannot prevent a trusted injected callback from itself making an extra call.
Such calls do not become passing, fully budgeted selection evidence.

Durable admission retains the original claim configuration across callbacks and
requires a pristine cursor for a new identity. Cached reads may use a consumed
cursor, but their callbacks cannot change its starting position or binding.
The actual result must match the original claim, and completion checks run after
the pre-commit checkpoint. A failed result transaction rolls back before the
ledger is halted, avoiding a nested SQLite transaction. Previously charged usage
and unresolved reservations are never refunded, and interrupted ownership is not
automatically replayed.

A callback after a valid result commit can cause the current return to fail and
halt the ledger; it cannot retroactively erase that already committed historical
result. A later unchanged cached read can still recover those exact bound bytes
without dispatch. This does not claim atomic revocation across stores, hostile
memory isolation, production approval or source authentication.

Run the focused tests with:
`python3 -B -m unittest discover -s tests -p test_selection_binding.py -v`,
and repeat with `-O`. Tests use actual ScriptedProvider, budgets and component
SQLite with synthetic source/authority fixtures. Independent call and SQL
observations cover preclaim/use/reserve/settle/final-snapshot/checkpoint drift,
consumed prefixes, retries, unknown charges and stable cached recovery. Removing
the process-local guard demonstrably yields selected results attributed to the
wrong script. Network and serving-load spies must remain unused.

Full retained-input suite/aggregate qualification requires the separately pinned
Tether source. Actual DSPy, external provider execution, production serving and
live acceptance remain unqualified.
