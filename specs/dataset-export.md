# One-item dataset projection (injected transport only)

This opt-in slice exports one currently retained materialized item into an
**already existing** Langfuse dataset. It neither creates datasets nor changes
training authority, activates serving, archives items, or deletes remote content.
The existing score HTTP transport and score-ledger authority are unchanged.
There is no concrete dataset HTTP client or live dataset qualification here.

## Authority and destination

`Export` requires the original Store, DatasetRegistry and RetainedSource, an
exact item, and a `Destination` binding HTTPS origin, project, dataset ID/name
and train/heldout split. A separate current `dataset_export` approval receives
the exact descriptor and destination; training/feedback grants do not grant
content export. Existing retained-use verification also requires this purpose.
Final source, cohort and withdrawal fences remain mandatory. An enrolled or
sealed cohort cannot export through this ownerless interface.

The remote dataset GET must prove project/id/name and pre-existing metadata
`runspindle_dataset_split` equal to the requested split. This module never adds
that metadata. Dataset-item readback alone cannot establish project identity.
Captured component-owned ReadViews bind the actual Store, registry, feedback
and optional source-observation files and connections. Auxiliary authorities must remain outside explicit read
transactions, so callbacks cannot pin a stale SQLite snapshot. Their physical identity
hashes enter the immutable intent; copied authority databases cannot resume the
same intent. This does not detect restoring old bytes into the same inode, and
is not a defense against arbitrary code modifying component-private internals.

## Durable progression

The existing Store.objects contains typed immutable intent, attempted-write,
readback and local-revocation records. Extra persisted fields are hashes only;
raw input/output is reconstructed transiently from the authorized registry.
All existing records are validated even when remote content is already visible.
A committed once-only attempt precedes POST. A missing/404 item after any
attempt is **uncertain**, not proof that the POST failed, and never permits a
blind repeat. Exact later readback can settle delivery. A previously delivered
item disappearing does not authorize recreation. Successful delivery means the
exact approved item is visible, not necessarily that this invocation wrote it.

A final current-authority check and injected POST share a Store transaction to
serialize the local sticky tombstone against a delayed local writer. This is
not an atomic transaction with the registry, feedback source or remote service.
Withdrawal can race an already in-flight effect; settlement rechecks authority
and denies stale delivery, but remote content may remain. Local revocation is
sticky and is **not remote erasure**. Cleanup requires a future separately
approved capability; archive and DELETE are not implemented.

## Exact content and API contract

Input must be finite canonical JSON, output exact UTF-8 text. Controls,
surrogates and Langfuse media markers are rejected rather than normalized.
Readback requires exact content/metadata/status, no trace/observation association,
and an explicit empty mediaReferences list. Normalization or unsupported media
fails closed. Requests carry timeout=10 and max_bytes=262144, and returned
bodies are bounded. The injected transport is trusted to enforce its deadline;
concrete dataset transport deadline qualification remains future work.

The API contract was checked against Langfuse 4.82.0 source
`50665566e515e5d7baf28a5ef3e19ac8e920914a`, specifically
`fern/apis/server/definition/dataset-items.yml` and `datasets.yml`:
GET `/api/public/v2/datasets/{datasetName}`, GET
`/api/public/dataset-items/{id}`, and POST `/api/public/dataset-items`.
POST is an upsert without remote compare-and-swap. Dataset names here are opaque
ASCII identifiers; no arbitrary path interpolation is accepted.
The existing shared dataset fixture remains SHA-256
`c8e0f7ea2d7ca6a8724dee27e213636d6553ce7601d22eed1f6f220f99407c98`.

## Verification scope

Tests use real signed local Tether fixtures and an injected in-memory HTTP
implementation: exact readback, uncertain acknowledgements, reopened state,
independent concurrent connections, revocation and callback mutation, conflicting
records, copied authority, wrong project/split and unsupported content. They do
not establish actual Langfuse dataset transport compatibility or remote
revocation guarantees. No external content upload or provider call is performed.
