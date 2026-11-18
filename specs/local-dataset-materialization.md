# Local consented gold dataset materialization

Component implementation; actual user data, remote dataset uploads, optimization
and deployment have not been performed. This extends the retained signed-source
bridge, not Langfuse's authority. No Temporal dependency or persistent job exists.

`DatasetRegistry.materialize` accepts an exact `RetainedSource`, opaque dataset
name, current materialization authority and permission reference. It derives the
input from the existing verified source bytes as an exact UTF-8 JSON string, the
output from the eligible gold output's exact bytes, and train/heldout from the
existing immutable family split. There is no caller-selected split, rewritten
answer or inferred input transformation. Non-UTF-8 and oversized examples reject.
The family digest hashes canonical `{refinery_source_family: family_id}`; upstream
lineage remains responsible for assigning related revisions the same family.

The shared v1 descriptor and Unicode fixture match Next's contract at
`58245e60fc7af62cb180323e8d96d39687a78a18`. The codec admits only bounded actual
JSON types, string keys, finite numbers, depth and text limits. Computing an item
identifier is not permission to copy content. Exact artifact, canonical input,
occurrence, family, dataset and locked split participate in immutable identity.

An isolated SQLite sidecar retains exact input/output, descriptor and a historical
permission-reference hash in one transaction. Current permission and retained
source validity are checked again before commit. Immutable triggers prevent
normal update/deletion. Empty versioned databases are not silently recreated.
Family, input hash and source occurrence cannot cross splits within one registry
and dataset. This is exact identity leakage prevention, not semantic deduplication
or coordination across independently copied registries.

Every `read` revalidates current retained lineage and material, exact stored bytes,
current item/purpose permission and original use authority. Only train items may
be used for optimization; heldout items are accessible only for evaluation.
Materialization does not itself invoke either. Projection eligibility flags and
historical receipts never replace the separate purpose-bound use check.

Authorized `withdraw` appends a permanent local tombstone. Re-materialization and
reads then deny; retained bytes are **not erased**. Signed source removal or target
invalidation also blocks fresh use. Remote deletion/archival/export is not part of
this module. Any future content upload requires separately reviewed exact-example
and destination authorization; opaque score metadata is a different permission.

Authority callbacks are trusted pure snapshots, not distributed transactions.
The component rechecks deterministic local source fences and local withdrawal,
but does not claim atomicity with an arbitrary external policy service or hostile
filesystem rollback. The registry is for owner-controlled local component use.

Tests use actual signed synthetic Tether admission/lineage, including permission
withdrawal, source removal, final-callback invalidation rollback, cross-split
rejection, immutable rows, reopening and a deliberate withdrawal-guard mutation.
No provider, model, remote API or actual customer content is used.
