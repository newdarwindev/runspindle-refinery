# Opt-in materialized GEPA learning admission

`Session.materialized_learning(store, api_version, identity, snapshot_sha256, *,
intent, provider, limits, per_call, budget_id, training_output_id, retained_use,
materialized_batches, checkpoint=...)` returns the existing ordinary Session.
It does not start compilation/evaluation or create another coordinator/store.
No framework import, provider dispatch, budget initialization or ownership claim
is performed by admission. Callers explicitly invoke `compile()` / `evaluate()`.

Both exact optimization and evaluation AuthorizedBatch values are mandatory.
They bind the retained snapshot, original selected examples, current callbacks,
permission references and physical local source/registry/feedback stores through
the existing Session binding. This factory selects GEPA explicitly; it offers no
synthetic-fixture mode, generic owner override or free-form constructor kwargs.
The original constructor and Bootstrap behavior remain unchanged.

- `intent="start"`: require pristine provider cursor and no existing experiment.
  Check current optimization/reflection and evaluation grants before any claim.
  After all callbacks, callback-free source/availability fences cover BOTH phase
  batches, catching a later callback withdrawing an earlier observed item.
  A final callback-free cohort-policy check for every admitted purpose also
  rejects a same-owner seal installed by a later callback.
- `intent="resume_evaluation"`: require exact existing owner in `compiled` or
  `complete` state and a valid compiled record. Check evaluation authority only;
  do not reauthorize optimization/reflection, restart a compilation, take over an
  interrupted claim or reset a budget. Final fences cover the evaluation batch.
  Actual deletion/revision of retained source lineage can still invalidate this
  phase; denial of the separate training-purpose grant is not source deletion.

Both paths retain constructor-bound routes, strategy/batch/binding identity,
original lifecycle row and provider cursor through checks and final fences.
Admission returns no permission token and caches no grants. Existing phase and
per-use guards remain mandatory; a withdrawal after admission can still deny the
next action. This is not atomic revocation across separate databases or proof
that arbitrary private callback state remains unchanged.

Tests use actual signed synthetic materialization and real durable component
claims/reports, with manually constructed compile/evaluation receipts. They do
not qualify actual DSPy/GEPA execution through this new API. The separately
reviewed native harness must exercise the factory before such a claim is made.
