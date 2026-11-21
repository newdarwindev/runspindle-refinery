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

A canonical owner-private directory and regular owner-only (0600), single-link
file are required; unsafe existing paths are rejected without chmod. SQLite
sidecar identity/permissions are checked as well. Existing schemas are admitted
before writable journaling settings are changed. No global umask or trust setting
is modified. An isolated SQLite sidecar retains exact input/output, descriptor and a historical
bounded historical receipt in one transaction. The receipt binds the exact original
lineage snapshot, collection receipt, selected event, Tether observation and
permission-reference hash; none grants current authority. Current permission and retained
source validity are checked again before commit. Immutable triggers prevent
normal update/deletion. Empty versioned databases are not silently recreated.
Family, input hash and source occurrence cannot cross splits within one registry
and dataset. This is exact identity leakage prevention, not semantic deduplication
or coordination across independently copied registries.

Every `read` revalidates current retained lineage and material, exact stored bytes,
current item/purpose permission and original use authority. Only train items may
be used for optimization; heldout items are accessible only for evaluation.
Materialization does not itself invoke either. The established `use_guard` and
callback-free final cohort check enforce upstream DSPy enrollment/heldout seals
with no policy owner: enrolled cohorts are not independently reopened here. Projection eligibility flags and
historical receipts never replace the separate purpose-bound use check. The
projection `use_allowed` indication may be reduced by an optimization probe and
therefore does not veto separately authorized heldout evaluation. Global use
denials must be represented in the original use/material/collection authority.

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

## Final feedback check for a single item

For Refinery #15 and #4, both materialization and use check the entire retained
feedback collection after the final policy callbacks. The individual projection
can still be current when feedback from a different selection in the same
collection has changed. The same callback-free check used for guarded batches
compares every retained feedback digest and any recorded task-source fence.
It runs before SQLite commit or byte release. A late invalidation rolls back a
new item or denies a read of an existing item while preserving its historical
bytes and receipt.

The regression uses two signed synthetic reactions for one exact output. The
final policy callback removes the other actor's reaction through pinned Tether.
Independent Tether reads observe the removal while the selected reaction is still
active. Both denial assertions fail on the base code and when the two final
checks are removed in a disposable source copy. These checks cover a change the
component can observe before returning. Concurrent updates after that read and
distributed transaction guarantees remain outside this qualification.

Reproduce with the exact Tether checkout from `dependencies/tether-feedback.json`:

```sh
RUNSPINDLE_TETHER_SOURCE=/path/to/pinned/tether python3 -B -m unittest discover -s tests -p test_dataset_materialization.py
```

The local checks use Refinery base `e2a1d45c2ba201f4476a7265b7a8c766d87ff694`,
Python 3.13.18 on macOS arm64 and the exact Tether pin
`1e8d2091c55dfad47fdc64ec2e73fcd946da9921`. Scaffold and changed-file Ruff
checks passed. Repo-wide static quality reported the same Linux-only
`os.O_PATH` diagnostic on both base and candidate.

```text
7a305663f4f9a10884698074946d9c902556f9a0a459156e4c08a177d017e7d6  refinery/dataset_materialization.py
36a32971452952c3b743e9d0fe1665f10a18c06aee14e0997da914e213c50eb7  refinery/dataset_batch.py
1e0da41be2e6b9bc7d0ebece3d8a2fef013bf0e5868ee3b2159c8972e084b4f3  tests/test_dataset_materialization.py
7b3fde62dda082970ceba5480409f47aff0163b66c98592709679464fd8d4f74  dependencies/tether-feedback.json
9cbe1331549216b953f2922f1e8321357ca82bb985e1ea595c87fcee8a8fdc84  requirements-quality.txt
```
