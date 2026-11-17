# Source-bound feedback invalidation

Partial Refinery #15 / parent #4. The original evidence described here used pinned
Tether `c3fbfb9ff5ed0483e522c93268ced9e40a6cb79d`, including its merged #31
source-observation store and its separate feedback-message invalidation store.
No live Slack, source/consent authority, training, deployment or migration is
qualified by these tests.

## Two different negative facts

Authenticated feedback-message changes/deletions are observed by existing
`SignedSlackFeedback.invalidate`. They preserve original feedback attribution and
judgment bytes while permanently making the message ineligible. This integration
updates the exact external source pin and closed loader inventory; it does not
copy Tether source or duplicate its HMAC/parser/store implementation.

A task-input edit/deletion is different. `SourceFeedbackGuard` connects the actual
pinned `SourceObservationStore` to an explicit trusted material/source resolver.
The resolver identifies the retained original, including installation, workspace,
channel, message, original task, actor, thread and normalized-intent digest. The
bridge compares it to the store's independently resolved original and exact
artifact target task/workspace/channel/thread. This bounded implementation only
supports original task revision 1. It does not invent a successor revision or
interpret an editor/deleter as the original actor.

The host resolver must authenticate the exact source/material mapping, including
installation. It is a trusted injected seam, not a provider-supplied assertion.
Existing material verification and independent collection, training and use
permissions remain mandatory. `unknown` means only that this negative ledger has
no retained fence; it does not establish freshness or grant any permission.

## Immutable receipts and fresh checks

Pass `source_guard` explicitly to `collect`. A version-2 collection receipt binds
each original source and its observed negative state. Fenced selections retain
attribution but produce no usable example through the existing quarantine path.
The original dataset, family mapping and split remain immutable. No existing
SQLite schema or stored receipt is rewritten.

A v2 snapshot requires a source guard at every retained-use check. Removing the
guard, changing the resolved source, observing a fence, or losing source access
fails closed. Resolver/material/consent callbacks execute before the final
callback-free reads of every source fence and feedback record. Later resolvers
cannot silently invalidate earlier selections after their final observation.
These reads are not atomic across databases; a change after the last read remains
a documented race, not a global dispatch barrier.

Once an identity has a v2 receipt in the same Refinery store, recollecting or using
that namespace/feedback ID as unguarded v1 is denied, including another revision.
Historical v1 objects remain readable. Standalone unbound v1 collection is retained
for existing synthetic/component users; it does not claim source-fence coverage.
Provenance erased in a different store, entirely new identities, malicious trusted
callbacks and hostile database mutation are outside this boundary. This is not a
global training-use authorization service.

## Upgrade and verification boundaries

The new Tether source uses feedback schema v2. Old v1 Tether feedback stores fail
closed and require that component's separately reviewed, quiesced migration;
this change runs no migration and provides no fallback. Source fences are a
separate existing Tether SQLite family. Runtime callers must provide the correct
persistent store and trusted mapping; tests use disposable files and invented
accounts only.

Run the focused suite with an external exact pinned Tether checkout:

```
RUNSPINDLE_TETHER_SOURCE=/path/to/pinned/tether python3 -m unittest discover -s tests -p test_source_feedback.py -v
RUNSPINDLE_TETHER_SOURCE=/path/to/pinned/tether python3 -O -m unittest discover -s tests -p test_source_feedback.py -v
```

Tests exercise real HMAC/SQLite, fresh-process replay, authenticated feedback
edits/deletes, source fences, forged signatures, wrong routes, missing consent,
original binding mismatch, callback-time invalidation, downgrade denial, immutable
lineage/families and a deliberate removed-fence mutation. Independent Admission
dumps, provider ledgers and socket observations assert no execution/network effects.
Scripted training denial records zero calls and zero claims. No real optimizer,
provider, live account, paid effect, service, backup or permission change occurs.

The original evidence above used the historical pin. Current dependency and the
explicit, fresh-consent path for retaining old snapshot provenance are documented
in [feedback revalidation](feedback-revalidation.md). Older receipt identities
are never silently rewritten or granted current use authority.
