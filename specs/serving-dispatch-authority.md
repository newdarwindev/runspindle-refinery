# Fresh approval at serving dispatch

Partial #17 and parent #6. The existing durable registry and disposable local
loader are reused. This is not a new production serving adapter or authenticated
production approval issuer, and no candidate is activated outside synthetic tests.

`Registry.begin` still binds immutable candidate/evaluation/policy/environment
approval and creates an intent. A stored intent is not perpetual authorization:
`Registry.load` now independently checks the current approval verifier before
claiming dispatch and after its before-load checkpoint, immediately before calling
the adapter. Only an actual string equal to the declared actor is accepted;
truthy values and foreign equality implementations are denied.

Each dispatch check verifies the exact content-addressed approval and candidate,
the candidate's actual byte digest, all approval fields, activation identity,
environment generation, predecessor and pending ownership. It invokes the trusted
verifier, then rereads the immutable objects, approval pointer and mutable intent/
environment ownership. A verifier that mutates its argument or another registry
binding cannot authorize dispatch under the old decision. Callback exceptions
produce a fixed denial; cancellation propagates.

The dispatch binds the exact prior activation ID, generation and candidate
digest, and rechecks them after the verifier callback. A replacement or rollback
can only proceed when the loader compares that identity atomically with its
actual active row. Matching program bytes from another activation do not permit
replacement. The registry's adapter interface supplies this complete identity.
The local synthetic loader still accepts digest-only direct calls using its old
fixture API.

A denial before the durable claim makes no registry or adapter write. A denial or
cancellation after the claim leaves the existing unknown outcome and reservation;
restoring approval does not automatically retry an uncertain load. An acknowledgment
lost after actual load still reconciles through independent readback without
loading again. Reconciliation of actual historical state is not a new approval,
and revocation does not automatically unload or stop a previously loaded program.
Rollback candidates receive the same fresh checks at their eventual dispatch;
revoked rollback cannot replace current loaded bytes.

These are bounded observations, not a cross-database/external atomic revocation
barrier. The authority callback and host-owned stores remain trusted. Actual issuer
verification, generation-scoped production grants, production target isolation,
real DSPy/provider serving and live acceptance remain unresolved. No approval
schema, durable schema, credentials, migration, workflow trigger, service or access
configuration changes. The two existing process-death fixtures now supply an
explicit synthetic current verifier so they still reach their intended crash
points; a separate fresh-process denial test covers missing authority.

`tests/test_serving_dispatch_authority.py` uses actual disposable SQLite registry
and loader with synthetic evaluator/authority seams. It verifies zero load-call
rows, exact database state where unchanged is promised, preserved loaded output,
fresh-process denial, checkpoint revocation, callback mutation, cancellation,
foreign identity rejection, lost acknowledgments and rollback. A deliberate
removed-dispatch-guard fault must fail the independent negative regression. Existing
four durable mutation checks remain required; only their exact approval-condition
anchor is updated to follow the stricter type check.

Commands (also require the repository's exact external Tether source for the full
suite):

```
python3 -m unittest discover -s tests -p test_serving_dispatch_authority.py -v
python3 -O -m unittest discover -s tests -p test_serving_dispatch_authority.py -v
python3 tools/check_durable_mutations.py
```

See `docs/serving-dispatch-authority-evidence.json` for actual results and unavailable
checks. No external provider calls, deployments, live account actions or production
qualification are inferred from these synthetic component tests.
