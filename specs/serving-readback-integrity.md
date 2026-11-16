# Serving readback identity and reconciliation integrity

Partial issue #17 and parent #6, extending `Registry.reconcile` and the existing
`LocalServing` adapter without introducing a production adapter or target issuer.

The previous reconciliation compared arbitrary adapter objects to a dictionary.
Python equality could accept a foreign object or boolean generation (`True == 1`).
On a rejected replacement, a dictionary containing only the predecessor digest
could release the pending reservation even if its activation/generation did not
identify the actual previous activation. Readback callbacks could also change
registry generation/intent while keeping the same pending ID, allowing a stale
observation to be committed.

Reconciliation now compares only exact dictionaries with exactly three string
keys, exact string activation/digest values and an exact integer generation.
Foreign equality methods are not invoked. Successful readback must identify the
requested activation, generation and candidate digest. Definitive rejection with
a predecessor must identify its latest historical active activation before this
generation, not merely contain equal bytes. With no predecessor, only `None`
observes absence. Malformed or inconsistent responses leave the outcome unknown.

Before calling readback, the registry captures exact candidate/predecessor bytes,
intent, pending ownership and prior activation identity. Inside its final local
transaction it rereads all those bindings before committing any observation.
Callback changes produce an error rather than a stale activation or reservation
release. Callback writes themselves are not undone; these remain trusted host
stores, not hostile-storage isolation. No transaction spans registry and loader.

Historical reconciliation remains separate from authorization to load. It does
not reauthorize or redispatch an unknown activation, including after restart and
revocation. Existing fresh dispatch checks and rollback admission remain intact.
The adapter is still trusted to read the requested environment; a correctly shaped
lie about another target is not authenticated by this check. Production account,
target identity, issuer and storage qualification remain open. There is no new
schema, migration, dependency, endpoint, credential or persistent job.

The new tests use real disposable SQLite registry/loader stores and synthetic
approval/evaluation seams. They observe load-call counts, preserved loaded output,
zero network attempts, wrong-identity/shape denial, callback mutation, exact
predecessor rejection, and fresh-process lost-ack recovery without a second load.
A deliberate removal of strict readback matching must break a negative test.
Existing tests additionally cover concurrent promotion, stale generations,
rollback and loader failure. These are component observations, not live serving
or DSPy/provider quality evidence.

Reproduce focused checks:

```
python3 -m unittest discover -s tests -p test_serving_readback_integrity.py -v
python3 -O -m unittest discover -s tests -p test_serving_readback_integrity.py -v
python3 tools/check_durable_mutations.py
```

The full suite requires the existing exact external Tether source named by
`RUNSPINDLE_TETHER_SOURCE`; its hashes and revision remain pinned in
`dependencies/tether-feedback.json`. Actual results and blocked checks are in
`docs/serving-readback-integrity-evidence.json`. Issue #17 remains open.
