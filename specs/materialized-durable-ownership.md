# Materialized batches bound to durable optimizer ownership

Opt-in component refactor, not yet actual framework qualification. Existing
legacy Session ownership bytes are unchanged when no materialized batches are
supplied. No runtime or historical evidence is removed.

`Session(materialized_batches=(AuthorizedBatch(...), ...))` binds the exact
canonical batch manifest hash, explicit phase and opaque permission-reference hash
and digests of the actual local registry/feedback/source file identities
(canonical path, device and inode) into the existing experiment ownership bytes alongside original lineage, optimizer
configuration and budget identity. A permission-reference hash remains historical,
not a reusable grant. Resuming the same run with a different batch or removing the
materialization binding conflicts with its retained owner record. A stale copied
registry or feedback database cannot silently resume that owner. Moving/restoring
authority files requires a separately authorized new run; no automatic migration
or hostile same-owner rollback protection is claimed. Raw paths are not stored
in the ownership record.

Every phase requires its exact manifest: optimization and GEPA reflection use the
train manifest; evaluation requires a separate evaluation manifest. Missing
heldout materialization does not fall back to unrestricted lineage. The selected
items must cover exactly the phase's actual examples, including original feedback
attribution, input/output bytes, family and split. Both manifests must bind the
same actual Store, lineage snapshot and original retained-use authority. GEPA's
existing compile-only restriction remains; this does not add GEPA heldout support.

The local materialization registry supplies the shared exact-byte mapping. Its
current item/purpose permission callback runs for each use. Existing Session
`use_guard` supplies the already bound experiment's enrollment/heldout owner and
original source/material/use checks. Generic `DatasetRegistry.read` still uses
owner=None and rejects enrolled cohorts. No public generic owner override or
replacement positive authority is introduced. Final shared callback-free source
fences and local withdrawals are checked after authority callbacks.

Before worker use, the owner thread captures canonical file identity and schema
for the existing registry and pinned Tether feedback/source stores. Each check
opens short-lived mode=ro/query_only connections on its using thread with SQLite
thread affinity still enabled. It does not use immutable=1, copy databases,
create tables or hold a WAL read transaction across callbacks. Private registry
and sidecar checks remain mandatory. Changed files, schema or handles deny, and
connections close on exceptions. The existing Store.worker_context owns the
optimizer ledger connection, unchanged. A trusted callback that itself uses a
foreign-thread SQLite handle fails closed; it is not replaced with permission.

This is an owner-controlled component boundary, not protection from a malicious
owner rewriting files/code or arbitrary external policy races. Callbacks must be
bounded trusted current-policy/material snapshots and safe on the caller thread.
There is no new database, coordinator, Temporal dependency, remote content upload,
credential discovery, paid provider or production activation.

Tests include real signed synthetic lineage and SQLite claims/dispatch receipts,
fresh Session and fresh-process checkpoint recovery, withdrawal, manifest substitution, generic-read denial
after enrollment, actual worker threads, private sidecar refusal, replaced files
and handle cleanup. Such fixture dispatch tests are not a claim that DSPy ran.
Actual pinned framework and fresh-process qualification remain a separate gate.

The regression suite also uses two independently signed targets retained in one
actual Tether store and a locked mixed-family snapshot: exact evaluation batch
claim/guard under the compiled candidate owner passes; another owner, swapped
split and extra phase examples deny. A later item callback can withdraw an
already-read item through another connection; final checks observe the WAL change.
