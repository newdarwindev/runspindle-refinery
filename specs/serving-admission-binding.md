# Approval admission callback bindings

Partial #6/#17 component behavior. `Registry.begin` records an intent, not a
successful serving load. Its trusted approval callback may execute arbitrary
application code, so checking bindings only before it returns is insufficient.

Admission now accepts the exact `Approval` type, captures its canonical bytes,
actor, candidate bytes, registry store and verifier, and checks those bindings
again after the callback. A changed actor, target, evidence or digest rejects
before an approval record, activation intent or environment reservation is
written. A replaced store or verifier also rejects. The candidate is read again
inside the transaction which records the intent. Existing generation/pending
compare-and-swap and fresh load-time authorization remain unchanged.

Tests use actual SQLite registry state and inspect complete dumps for zero
admission writes. Negative cases mutate every approval field or replace the
registry's store/verifier; a foreign approval subclass must not execute its
validation hook. The original callback target-substitution regression fails
against the former code because it creates an intent in the changed environment.

This does not authenticate the injected callback, undo effects caused by that
callback, or make an external authority and SQLite one atomic transaction. An
approved callback can change and restore state between checks. No live provider,
production target, credential, deployment or new protocol version is involved.

Bounded verification:

```sh
python3 -m unittest discover -s tests -p test_serving_dispatch_authority.py -v
python3 -O -m unittest discover -s tests -p test_serving_dispatch_authority.py -v
```
