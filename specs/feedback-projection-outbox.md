# Authoritative local feedback projection and Langfuse outbox

The shared contract and ASCII/Unicode fixture in `contracts/` match Next commit
`04d5891b661482ed989ad648dbdd1ed7160b2a91` in
`newdarwindev/runspindle-next-experiment`. The encoder is not authentication.
Occurrence, actor and exact retained target determine identity, not output content
alone. Revisions are immutable; source revocation is sticky. Dataset examples,
family splits, source content and model deployment authority stay local.

## Local authority

`RetainedSource` accepts an existing immutable collected snapshot and exact
selection, the real pinned Tether store and an existing `RetainedFeedbackUse`.
It checks retained event/actor/target association and current source state. The
host must explicitly authorize the new `feedback_projection` use purpose and a
separate destination-bound projection policy. A copied remote JSON object cannot
enter this bridge. `use_allowed` is additionally checked against existing local
optimization-use authority. These projection flags never replace the optimizer's
own fresh exact-run authority check. The complete projection policy is read again
after use callbacks; any policy change rejects the observation before persistence.

Only opaque hashes, revision, state and policy-derived booleans enter the outbox.
The fresh policy authorizer receives the opaque feedback identity and explicit
adapter destination binding. It must actually establish permission to disclose
that metadata to the configured HTTPS origin/project/trace; neither a thumbs-up nor existing
collection consent supplies export permission. No raw prompt/output, actor ID,
channel identifier, credentials or error diagnostics are persisted by this ledger.
Source/content materialization is not implemented or implicitly permitted.

## Transactions and recovery

`ProjectionLedger` uses a separate SQLite sidecar with affinity on, WAL/FULL
synchronous writes, an explicit schema, immutable revision triggers and bounded
record count. Revision, head and outbox insertion commit together. Unknown schema
is rejected, not migrated or repaired. Instances are bound to one destination;
callers own connection lifetimes and may reopen after process loss. Filesystem,
host callbacks and SQLite are trusted; this is not a hostile-process sandbox or
cross-database atomic revocation protocol.

`capture` re-observes local state. Unchanged observations do not create revisions.
A tombstone never becomes positive again. `dispatch` reads remote state, rechecks
local authority, records the attempt before writing and requires exact readback.
It rechecks source authority after the write and settles only the same current
revision. A stale worker cannot acknowledge a newer revision. Two callers may
perform harmless reads; the pending-to-attempted transaction admits only one first
write. Caller-owned transport must honor the explicit timeout/output bounds.

Failure before any write remains pending and can replay after an outage. Once a
write was attempted, absent/failed/eventually-consistent readback remains uncertain:
**there is no blind re-POST**, lease-expiry assumption or automatic retry authority.
Later exact readback can settle the retained attempt without another write. An
indefinitely unobservable attempt requires operator reconciliation. Revoked or
superseded old scores can remain remotely visible while offline; their local
history and the newer revision remain available. No remote cleanup success is
claimed, and cleanup would require its own permission.

## Langfuse mapping and compatibility

The injected HTTP adapter issues one `POST /api/public/scores` with the immutable
projection ID, authorized trace ID, `BOOLEAN` numeric value 0/1, source `API`, fixed
name and projection metadata. Acceptance is asynchronous, not proof of persistence.
[Official create-score schema](https://github.com/langfuse/langfuse/blob/main/fern/apis/client/definition/score.yml).

Readback uses `GET /api/public/v3/scores` filtered by ID, requesting details and
subject. It requires one result without another page, the exact project/trace,
name, API source, boolean value and projection metadata. HTTP errors are not
absence; an empty list is merely not yet visible. No deprecated-v2 fallback.
[Official public API documentation](https://langfuse.com/docs/api-and-data-platform/features/public-api).

The host supplies an explicitly authorized transport whose declared canonical
HTTPS origin must match the adapter before and after every request. Origin is
bound into the destination alongside project and trace, preventing distinct
self-hosted instances with identical identifiers from sharing export authority.
The injected transport remains trusted to route requests to that declared origin. No credential loading,
network client defaults, live requests, services, Temporal dependency or SDK
installation occur here. Synthetic tests use the real retained-feedback bridge
and local stores with a fake HTTP transport. They do not qualify a live Langfuse
installation or close external capture/identity prerequisites.
