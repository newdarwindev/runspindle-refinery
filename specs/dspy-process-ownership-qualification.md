# Actual framework two-process ownership qualification proposal

Run the separate bounded `python tools/offline_dspy.py --prepared <reviewed-dependencies>
--suite ownership --report <new-receipt.json>` only in the existing reviewed Linux ARM64 isolated lane. This
source proposal is not runtime qualification evidence and does not close #16.
The unchanged adopted closure, no-network container, synthetic-only child
environment, read-only mounts, resource limits and 120-second outer timeout apply.

Two fresh children bind the same real SQLite store, immutable snapshot/config/script,
Session identity and shared budget. Both signal readiness, then the parent releases
a common start barrier. `Session.compile()` and the existing transactional claim
logic choose ownership; this harness does not implement its own lock or claim.
Transparent wrappers call the original BootstrapFewShot.compile and scripted
provider.complete, independently counting actual entries and dispatch.

The winner pauses at the existing first `after_budget_reservation` checkpoint,
inside genuine framework compilation, before its first provider effect. At this
point one unresolved attempt and charge must be durably present. The loser is
rejected with no compile entry or provider effect. Its initial exact rejection may
be `interrupted_or_owned_dspy_phase` (`recover_compiled`/`claim_compile` sees the
owned row) or `lifecycle_claim_changed` (the winner commits between `guard`'s two
row observations). No arbitrary failure is accepted. After the reservation marker,
a second loser attempt must return exactly `interrupted_or_owned_dspy_phase` and
leave ledger/attempt bytes unchanged. The parent independently reads those bytes
and validates the loser receipt before releasing the winner.

The winner then completes the actual wrong-trace/retry path: exactly one compile
entry, two provider dispatches, two settled attempts, two charged calls and one
stored compiled object. A third fresh interpreter restores persisted virtual time
and recovers the same compiled candidate/report, with framework access forbidden,
zero new effects and byte-identical ledger/attempt evidence. Fresh PIDs are matched
to launched process objects and excluded from exported reports. Worker roles and
actual initial/retry rejection reasons remain observable; role and initial reason
may vary across valid interleavings. Independently validate repeated runs and compare
stable candidate/configuration/ledger/count invariants, not raw report equality. No held-out
identity may dispatch during this compilation-only ownership test.

Barriers have explicit 20-second bounds; child completion has 25-second bounds and
cleanup kills/reaps any still-running owned child. The 120-second outer deadline
always takes precedence, so per-stage maxima are not promises of additive time.
Temporary stores/markers/captures are removed on success or failure. No timeout is
a skip or an automatic bound expansion. Source-unit tests are synthetic receipt
and orchestration oracles only: extra effects, lost pending reservations, multiple
owners, changed recovery evidence and reused PIDs must fail. They do not import
DSPy. Actual execution awaits independent review and native evidence, after the
corrected adverse-path campaign succeeds. No general multi-owner budget-capacity,
reflection optimizer, authenticated live feedback, live provider or deployment
qualification is claimed.
