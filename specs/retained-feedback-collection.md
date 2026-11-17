# Explicit retained-feedback collection

Partial Refinery #15 implementation over actual Tether/Refinery SQLite components,
not live collection or a production authentication claim. Existing
`Store.commit_lineage` remains the only dataset collector. No outbox, serializer,
provider or optimizer is duplicated. Tether source is pinned to
`670989839a0fccbcf2014d01609778ae6e4df424` in
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
future dataset use must revalidate selections/permissions. The durable evaluation
and selection paths now enforce the separate [use-time eligibility gate](retained-feedback-use.md). Old snapshots remain
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

Current source-bound receipts and the newer external dependency pin are described
in [source-bound feedback invalidation](source-feedback-invalidation.md). Earlier
receipt/pin evidence above remains historical; no stored receipt is rewritten.


## Bounded preparation and cooperative cancellation

The existing exact Tether source pin, module hashes and collection/use consent
checks are unchanged. Dependency verification now owns one Git child at a time,
accepts at most 128 output bytes, and waits at most five seconds for output and
leader completion. It discards stderr and reports a fixed failure category.
A nonblocking per-instance verification lock rejects overlapping calls before
launching another child and releases on success or exception. This does not make
module import, callback state or SQLite access generally thread-safe.
Failure closes the owned stdout descriptor, requests leader termination if needed,
and waits at most another 0.2 seconds. Inherited descriptors cannot extend that
wait. A failed Git verification permanently fences that dependency instance; no
replacement invocation is silently started. Creating a new instance is not proof
that an earlier descendant terminated. This is neither process-group cleanup nor
a sandbox for hostile executables, filesystems or operating systems. Process
creation itself and uninterruptible kernel operations are not hard-time bounded.

Manifest reads are capped at 64 KiB and each of the exact thirteen pinned modules
at 1 MiB. A no-follow, nonblocking descriptor is checked as a regular file before
reading at most limit+1 bytes. Symlinks, FIFOs and oversized files reject before
compilation. Reads and retained source memory are bounded, but a slow regular-file
filesystem operation is not forcibly interrupted. Existing compile-from-verified-
bytes/import-identity checks remain mandatory. The implementation requires the
POSIX no-follow/nonblocking regular-file facilities used by this component's
isolated Linux lane; it does not install a platform fallback.

`collect(..., cancel=event)` optionally accepts an exact `threading.Event`.
Absence preserves existing behavior. This token is not an authority callback:
checks call the Event class implementation directly, reject foreign/subclass tokens,
and do not trust a caller-defined `is_set` method. Trusted callers must not mutate
Event internals. Cancellation is cooperative, sampled before and after dependency
verification, while preparing selected records, around consent/evidence checks,
and at the existing transaction checkpoint including after receipt insertion.
Observed cancellation before commit raises a fixed error and rolls back new source,
output, feedback, family, snapshot and collection/revalidation receipt rows together.
Previously committed history is not removed. Tests reopen stores in a fresh process
to independently observe the rollback, and permit a later explicitly authorized
retry with a cleared token.

A currently executing synchronous material/consent resolver cannot be forcibly
interrupted by this API. Cancellation can race the final check and SQLite commit;
a token set after commit does not undo the committed snapshot or turn its return
into a claim of rollback. No signal handler, worker, recurring job, network call,
provider dispatch or new permission is introduced. This bounded partial slice does
not qualify a production source-material or consent service and leaves #15 open.

Focused command with the unchanged prepared dependency:

```
RUNSPINDLE_TETHER_SOURCE=/path/to/exact/ae5ac216 python3 -m unittest discover -s tests -p test_bounded_collection.py -v
```

Repeat under `python3 -O`. Tests use actual owned Python stand-ins for hung/noisy
Git, actual bounded files/FIFOs, actual Tether/Refinery SQLite and provider ledgers,
and default-denied sockets. A deliberate removal of only the last cancellation
check produces independently observed forbidden dataset persistence under unchanged
consent gates. These are isolated component assertions, not native runner, live
provider, global revocation or universal process-termination qualification.
