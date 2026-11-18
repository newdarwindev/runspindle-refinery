# Guarded local materialized data-use batches

Component implementation, synthetic signed-source tests only. No DSPy execution,
remote dataset upload, model promotion or persistent service is introduced.

`dataset_batch.capture` reads 1–100 explicitly selected materialized items through
`DatasetRegistry.read`. The caller supplies current purpose-bound authority and
its opaque reference again. Items must belong to one named dataset and one
purpose/locked split; duplicate item, source occurrence and exact input identities
are denied. Ordering is deterministic by immutable item identifier.

The returned canonical manifest binds the complete descriptors, original lineage
snapshot hashes, historical materialization receipt hashes and use purpose. It
contains no raw input/output or serialized grant. Persisting these manifest bytes
is left to the owner-private caller; a hash or previously returned manifest never
authorizes current use. `dataset_batch.read` repeats all checks and requires byte
identity with the expected manifest before returning exact local input/output.

After every authority callback has finished, callback-free final fences check all
selected source receipts' Tether evidence and source-observation states, upstream
cohort policy and local withdrawal. Thus a later item callback cannot silently
invalidate an earlier item in the batch. Existing DSPy-enrolled/sealed cohorts
remain denied with owner=None: this is not permission to reopen heldout data or
borrow an optimizer's policy identity. Original use/material authority remains
mandatory; Langfuse projections and thumbs-up gold labels are not consent.

This is a guarded data-use boundary, not a durable optimizer adapter. An eventual
consumer must call it at each data-use boundary and separately retain the existing
optimizer budget, enrollment and heldout policy. It does not solve external
policy races, hostile owner/database modification, interprocess SQLite ownership,
or distributed atomic revocation. Callbacks remain trusted pure policy snapshots;
there is no claim of a simultaneous transaction across the independent stores.
Raw returned bytes cannot be revoked after a caller has received them. The batch
must not be serialized as a substitute for future authorization checks.
