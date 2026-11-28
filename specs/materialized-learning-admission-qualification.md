# Actual-framework admission integration harness

The existing signed mixed materialized GEPA success harness now calls
`Session.materialized_learning` with explicit `start` for compilation and
`resume_evaluation` for heldout evaluation and fresh completed replay. Intent is
never inferred from database contents. No runtime, quota, timeout, preparation,
provider, owner, metric or original five/two/zero effect oracle is replaced.

Each phase observes exactly one call through the original factory and requires
its actual returned Session, unchanged logical persisted checkpoint, pristine
provider cursor and zero attempted provider completion, GEPA.compile or
Predict.forward executions during admission. It does not import a framework to
observe it. The fresh replay import-rejection guard still precedes component
imports; its zero-import/effect requirement remains independent. Admission
receipt fields and cross-phase input checkpoint hashes are strictly validated.
Source bindings include the factory and observer regression tests.

Component tests exercise real signed local inputs, original factory and mutation
oracles for bypass/substitution, provider/framework entry and checkpoint writes.
Framework-effect mutation tests use doubles solely to test the observer, not to
claim actual DSPy execution. Actual native execution through this changed source
is pending separate independent review/full gates/qualification; prior successful
five/two/zero receipts do not qualify the new factory path automatically.
