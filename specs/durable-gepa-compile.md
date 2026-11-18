> Historical compile-only milestone. The separately reviewed [GEPA heldout extension](gepa-heldout.md) adds component evaluation support; actual new-framework qualification is still pending.

# Durable GEPA compilation (component implementation)

This extends the existing `durable_dspy.Session` state machine with the sealed
`strategy="gepa"` and an explicit training output identifier. It is **compile-only**:
GEPA heldout evaluation is rejected before framework import. No new native GEPA,
fresh-process optimizer, live-provider, promotion or concurrency qualification is
claimed by the lifecycle fixtures. Native execution of the parent process-local
adapter is tracked separately; failed native receipts remain authoritative.

## Identity and authority

Bootstrap remains the default. Its lifecycle string, identity prefix, binding
bytes, configuration algorithm, stored result shape and optimization/evaluation
authority set are unchanged, with byte goldens captured from PR80 source 2bcf357.
GEPA uses a distinct lifecycle, identity prefix, stored result kind and exact
`GEPACompiled` class. The old Bootstrap candidate decoder is not widened.

Pure GEPA binding includes the selected training example, immutable synthetic
script, budget/reservation limits and reviewed runtime/source closure identity.
Computing this identity is not runtime admission: new compilation verifies the
supported Python 3.13 preparation and framework before claiming ownership. A
completed or blocked checkpoint is read without importing DSPy or executing the
runtime. No caller-supplied manifest or validator chooses the strategy.

Every GEPA compilation/recovery authority check requires both optimization and
reflection use permission. Reflection does not inherit optimization consent.
The exact strategy, lifecycle, object kind, selected training identifier, key and
binding are sealed before and after every authority callback. GEPA authority routing identities are fixed at construction in a component-owned
weak registry, not a resettable Session attribute. These exact callables are
required across checks, checkpoints and provider effects; legitimate revocation
is observed through the original callback, never by replacing its routing.
Tests inject synthetic authority functions at the constructor seam, without a
production injection or reseal API. Unknown or
changed selectors fail before reservation/provider effects; mutation after a
reservation still denies dispatch and retains the charge. Claims and provider
cursor are rechecked after callbacks. Existing family
partition enrollment and cumulative shared SQLite budgets are reused. Runtime
identity, candidate strategy or selected example changes cannot recover an old
claim under a different configuration.

## Transaction-bound request evidence

An Attempt retains a request artifact digest, not its instruction/payload
preimage. A blocked run has no successful candidate carrying the proposal, and
an interrupted reservation may have no completion at all. Consequently each GEPA
reservation stores a bounded `(Request, instruction, payload)` witness in the
**same SQLite transaction** as the charged ledger and attempt row. Witness write
failure rolls back that reservation before provider dispatch. A checkpoint after
reservation leaves both witness and charge durable.

Witness objects are immutable and keyed by the exact experiment and budget
reservation ticket. Checkpoint verification requires the exact witness/reservation
set, recomputes every domain-separated request digest and enforces ordered request
indices and the selected training case. Reflection payloads must reference an
earlier settled safe incorrect initial-instruction receipt. Changed instructions
must be attributable to an earlier settled reflection completion and immutable
script bytes. Neither a matching digest alone nor an unresolved receipt grants
this authority. Unknown/terminal evidence cannot authorize later requests.

Existing candidate decoding additionally binds the complete ordered successful
receipt history. Unknown provider effects preserve unresolved charges and the
first terminal reason. Interrupted ownership is not resumed by fabricating a
completed record or refunding a reservation. The generic maintained Store now
admits only exact registered dataclass types, not an unrelated class with the
same name; legitimate existing encodings remain byte-compatible.

## Verification and remaining work

The source tests use real SQLite, the real metered synthetic dispatch transport,
and explicitly constructed component results, **not a substitute GEPA engine**.
They exercise success/blocked replay without runtime effects, revoked reflection
permission, callback mutation, missing/altered witnesses, recomputed foreign
associations, reservation rollback, interrupted unknown charges, script/runtime
identity changes and Bootstrap byte compatibility.

Locked baseline/candidate heldout evaluation, explicit recovery-clock restoration,
actual multi-process compile/evaluate/replay qualification and strategy-specific
concurrency qualification remain separate implementation/acceptance work. Current
component storage assumes trusted SQLite and immutable retained source; it is not
hostile-storage, power-loss or atomic cross-service revocation proof.
