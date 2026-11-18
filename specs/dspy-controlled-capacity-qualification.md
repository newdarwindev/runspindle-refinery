# Actual-framework controlled-overlap capacity proposal

This source-only campaign addresses a capacity boundary distinct from Session
ownership. It does not claim simultaneous reservation acquisition, live providers,
reflection execution, authenticated feedback or complete issue #16 acceptance.
Use the reviewed lane's existing launcher with `--suite capacity --report <new.json>`.
The exact adopted closure, no-network/read-only container, synthetic child
environments, resource limits and 120-second outer timeout remain unchanged.

Each of two variants creates a fresh real SQLite store. Two fresh processes use
independent optimizer controllers/providers and separate experiment IDs, but the
same immutable fixture, script/configuration and SQLiteBudget identity. Calls are
limited to one. Synthetic completion latency is zero, keeping independently
constructed VirtualClocks aligned without hiding clock-regression checks.

1. The first actual BootstrapFewShot compiler reserves one call. Its transparent
   provider wrapper calls the original ScriptedProvider.complete, observes one
   effect, and pauses before returning the completion for settlement.
2. Only then does the parent launch the second actual BootstrapFewShot compiler.
   Its reserve must deny `calls_exhausted`, with zero provider effects and no
   second attempt row. Both actual compile entries are independently observed.
3. Before release, the parent requires the same ticket/owner/reservation/counters
   and exact attempt bytes. The only allowed ledger transition is reason
   `None` to `calls_exhausted`, checked by hashing all ledger fields except reason.
4. The first controller resumes settlement. Known usage removes pending capacity
   and records one settled attempt with tokens/cost one. Unknown-token usage keeps
   the unresolved reservation, records the unknown receipt, exposes null usage,
   and retains the earlier global exhaustion reason. Both results remain blocked
   with no candidate. A known framework wrapper reason `optimizer_failure` is
   permitted only alongside the exact independently verified durable stop.
5. Both stopped controllers retry reservation and must be denied without changing
   any ledger or attempt bytes. Parent observations bind the exact launched PIDs,
   source/configuration and final stored evidence. Exported receipts omit PIDs.

The first in-flight hold is bounded at 45 seconds; launch/child waits at 25 seconds.
The outer 120-second timeout takes precedence over both variants and cleanup, so
these maxima are not additive execution guarantees. Every started child receives
kill/reap attempts even when an earlier cleanup fails. SQLite inspection connections
are explicitly closed and temporary stores/markers/captures removed on exit.
No timeout is an automatic pass, skip or permission to expand the bound.

Source tests are synthetic oracles and orchestration checks only. They deliberately
inject extra effects, lost reservations, fabricated settlement, arbitrary failures
and cleanup errors. A bad second-worker receipt must never release the held first
worker. Native exact-source execution and independent full gates remain pending;
existing historical receipts are unchanged. This controlled overlap demonstrates
shared capacity while one provider effect is awaiting settlement, not randomized
scheduler stress or simultaneous acquisition fairness.
