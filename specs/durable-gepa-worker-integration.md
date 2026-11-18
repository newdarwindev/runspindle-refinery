# Compile-only durable GEPA / worker ownership integration

This reconciles the separately reviewed compile-only Session strategy with the
worker-owned Store correction. It does not implement heldout evaluation or claim
actual GEPA, fresh-process, external-feedback authority or production acceptance.
Prior successful and failed receipts remain unchanged.

## Reconciled boundaries

- Keep the constructor-lifetime authority registry and exact strategy/candidate
  identities from the durable proposal. Optimization and reflection permissions
  remain separate original callbacks; a checkpoint cannot replace their routing.
- Keep worker contexts, thread affinity, cleanup and fixed-profile overlap denial
  from the worker correction. The same Session and Store objects are retained;
  direct authority `Store.get()` / `store.db` reads resolve to the admitted worker
  connection. No callback is substituted with a permissive worker equivalent.
- Reserve and persist the exact GEPA request witness in one worker transaction.
  A failed witness write rolls back before dispatch. Post-reservation revocation
  or interruption retains the charge/witness and prevents further effects.
- Preserve pure source identity for checkpoint recovery, including storage,
  durable strategy and evidence code. New execution still requires supported
  runtime admission. The original Bootstrap identity and authority are unchanged.
- Preserve the original worker diagnostic harness and strict actual qualification
  oracle. Heldout methods remain explicitly unavailable for GEPA.

## Component verification

Five additional fixtures alternate main-thread ownership/commit with real worker
thread task and metric execution, using the original metered synthetic transport.
The reflection step and explicitly constructed candidate remain component fixture
operations, not an actual optimizer run. The successful fixture demonstrates both
original authority callbacks running on worker and main threads, atomic witness
records, durable commit, and replay with zero effects and framework/runtime entry
forbidden. Negatives cover worker checkpoint revocation plus routing replacement,
worker witness-write rollback, unresolved provider effects recovered without
refund, and heldout rejection before framework entry.

This only admits connections owned by Refinery Store. External authority clients
or other components' database connections are not implicitly made thread-safe;
they require their own reviewed execution/ownership contracts and remain
fail-closed if their use is unsupported. Actual durable GEPA qualification and
fresh-process recovery are separate acceptance steps after source review and
complete native gates.
