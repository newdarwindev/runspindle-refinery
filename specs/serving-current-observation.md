# Read-only observation of current serving state

Partial #6/#17 functionality, separate from production target qualification.
`Registry.reconcile` preserves historical outcomes: a completed activation stays
historically active even if a different actor later changes the loader. Rechecking
that history is not a current serving health check.

`Registry.observe(api_version, environment, adapter)` instead calls the trusted
adapter's actual `readback` and returns a bounded versioned observation. It never
loads, approves, promotes, retries, rolls back or rewrites activation history.

The `runspindle.serving-observation.v1` envelope contains environment, registry
generation, active candidate key, pending activation ID, expected and observed
candidate digests, and one status:

- `empty`: no registry active candidate, no pending activation and actual absence.
- `current`: no pending activation and exact observed activation ID, generation and
  candidate digest match the recorded active activation and verified bytes.
- `drift`: available, well-shaped readback differs from the recorded active state,
  including missing actual bytes or a different activation with identical bytes.
- `pending`: registry ownership is unresolved; even matching newly loaded bytes do
  not turn observation into successful reconciliation or release the reservation.
- `unavailable`: readback raised or returned invalid types/identity. No raw callback
  diagnostic, malformed payload or private canary is included in the result.

Unknown expected/observed digests are `None`, never fabricated. The target passed
to readback is exactly the caller-selected validated environment. Empty or pending
registry state is not inferred from another target.

The method captures validated registry state, active candidate bytes/identity,
active activation identity and any pending intent in a local transaction. After
readback it checks the same bindings in another transaction. Changed/corrupt
registry state rejects rather than committing a stale success. Callback mutations
are not undone: callbacks and stores remain trusted, and no transaction spans both
stores. A change after the final check or change-and-restore between snapshots is
not prevented. A result is a point-in-time observation, not a freshness guarantee,
authenticated provider proof or authority to execute.

The existing actual SQLite `LocalServing.readback` is exercised alongside
controlled unavailable/malformed/racing callbacks. Tests inspect both stores and
load-call counts, actual changed output, lost-ack pending state, same-digest identity
mismatch, interruption and fresh optimized-process readback. Removing the actual
match condition falsely labels drift current and fails the independent assertion.
Network calls and reloads are forbidden; no production service is contacted.

```python
observation = registry.observe(VERSION, "local", adapter)
# Inspect observation["status"]. This does not authorize recovery or a new load.
```

```sh
python -m unittest discover -s tests -p test_serving_observation.py -v
python -O -m unittest discover -s tests -p test_serving_observation.py -v
```

Existing full-suite dependency/toolchain requirements remain unchanged. Production
identity, native/provider serving, deployments and live qualification remain open;
this read-only API does not close the parent issues or create a service/schedule.
