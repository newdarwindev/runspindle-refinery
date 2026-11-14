# Explicit retained-feedback collection

Partial Refinery #15 implementation over actual Tether/Refinery SQLite components,
not live collection or a production authentication claim. Existing
`Store.commit_lineage` remains the only dataset collector. No outbox, serializer,
provider or optimizer is duplicated. Tether source is pinned to
`17c4e8490eabe8876de8c4508ab6aa4d54af8c78` in
`dependencies/tether-feedback.json` with hashes for every package module.

## Inputs, authority and mapping

`refinery.feedback_collection.collect` requires a verified `TetherDependency`, an
actual FeedbackStore instance from that loaded source, and an explicit tuple of
1–100 SelectedFeedback records. Each names namespace, feedback identity, exact
revision, event digest and gold rule. Duplicate selection identities are rejected.
There is no scan-all, reaction interpretation, unsolicited collection or implicit
training permission. A tombstone is not a judgment: select its original judgment
revision to retain exclusion evidence.

An injected resolver supplies Material containing exact SourceRevision,
OutputRevision and a provenance reference. A separate trusted verifier must approve
that target/material pairing; it defaults to deny. Output ID, revision and bytes
must match Tether's immutable artifact target, and source ID/revision/hash must
match the output's source linkage. Tether does not retain original task input bytes
or authenticate that material relationship; actual Core/source adapters remain an
explicit unmet production dependency. Synthetic callbacks cannot establish it.

Collection and training consent are separate callbacks and references. Both must
return exactly True for the actual immutable lineage batch digest. An event's
presence, retained transport confirmation or retention grant never supplies either
permission. This function persists an approved dataset only: it does not execute
training/evaluation, contact a provider or authorize subsequent use indefinitely.

## Revisions and transactional evidence

The adapter reads exact selected records through Tether's API and maps their
verified actor to the existing verify_actor seam. Feedback IDs derive from the
immutable namespace/feedback/revision/event digest. Only a selected active current
revision can contribute an example. Stale, deleted and conflicted selections retain
original judgment bytes but mark target unavailable, so existing dataset logic
quarantines them. An edited correction is a new immutable revision, not overwrite
of an older result. Existing cross-batch conflict and family-lock rules remain.

Before committing source bytes, the existing collector's transaction checkpoint
revalidates dependency files/import identity, all selected Tether evidence, source
material authority, and both exact-batch permissions. It rereads dependency and
selected evidence after all callbacks, immediately before provenance insertion,
so a callback cannot change feedback and leave an old selection eligible.
Failure rolls back source,
output, feedback, family mapping, snapshot and provenance receipt together. The
selection receipt is stored through existing Store.put in that same transaction;
its digest becomes the lineage snapshot's collection_evidence reference. It binds
selected records, observed state/head, source mapping references and dependency
commit, plus separate collection/training references.

This is not a distributed SQLite transaction. Tether or external authority can
change after the final read. Receipts identify observed state, not timeless truth;
future dataset use must revalidate selections/permissions. Old snapshots remain
immutable historical records and are not retroactively authorization-valid or
automatically purged. This bridge does not install a global training-use gate.
Component-owned files/processes are trusted; hostile database/process mutation,
production proof validation and live consent systems remain unqualified.

## Reproducible preparation and tests

Prepare an external checkout of the exact pinned Tether commit, then set
RUNSPINDLE_TETHER_SOURCE to that directory. Missing source, wrong HEAD, altered
module bytes, symlinked package/files, preloaded unrelated Tether modules or replaced
module identities reject; no install, network fetch, bytecode cache or import
fallback occurs in verification. Verified source bytes are compiled from the
snapshot and the package search path is empty. Use one dependency instance per
process. This is a code-integrity boundary, not a sandbox against trusted callbacks.

The existing isolated validation workflow checks out that exact Tether commit using
the existing pinned checkout action, with persist-credentials:false, into ignored
`.feedback-deps/tether`. No token scope, service, image or runner policy changes.
Local command: RUNSPINDLE_TETHER_SOURCE=/path/to/pinned/tether python3 tools/validate.py.
Repeat tests with python3 -O -m unittest discover -s tests.

Tests use real Admission, ProviderJournal, FeedbackStore and Refinery Store with
invented records and synthetic proof/authority/provider seams. Provider request
ledgers remain unchanged during collection and socket creation is denied. They
cover exact replay/fresh-process dataset readback, dual-consent denial/revocation,
source-target mismatch, edit/delete/conflict exclusion, family rebinding, changed
feedback during approval, dependency mutation and a deliberately removed commit
guard producing independently observed unauthorized persistence. These are local
component assertions, not live-chat, actual-training or full-system acceptance.
