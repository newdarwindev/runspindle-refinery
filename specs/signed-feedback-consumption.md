# Signed feedback consumption candidate

Partial Refinery #15 integration, pending Tether PR #28 landing. This local
candidate pins published Tether commit
`040b71f2213a34f34e6b6a40124ed1164b93ce30`, not an assumed future main revision.
Do not publish this pin or merge the candidate until dependency disposition is
resolved by the coordinator. Tether's HTTP/TLS PR #27 is not included.

## Exact dependency mapping

`dependencies/tether-feedback.json` and `TetherDependency.COMMIT` identify the same
commit. All eleven loaded Python modules have exact SHA-256 pins. Existing eight
modules are retained; `slack_wire`, `slack_ingress` and `slack_feedback` are loaded
in dependency order after `feedback`. This expands the verified source closure,
not the import search path. Missing/changed source, wrong HEAD and foreign imports
still fail closed. No `slack_upload` or HTTP transport is loaded. The existing
component checkout ref changes to this same commit; runner labels, permissions,
actions, partition inventory and quality baseline are unchanged.

Compared with the previous pinned Tether commit, the closure additionally includes
already-reviewed admission post-lock authority verification and sanitized exception
handling, plus the shared signed-message verifier and signed-feedback producer.
Existing SQLite schema and FeedbackStore evidence contracts remain compatible;
full old collection/use/schema tests run against the new pin as well as new tests.
No source is vendored and no runtime package is installed.

## Actual end-to-end path

`tests/test_signed_feedback_collection.py` constructs independent provider-shaped
Slack raw bodies and v0 HMAC signatures with synthetic keys, then invokes the
verified dependency's actual `SignedSlackIngress`, `ProviderJournal` and
`SignedSlackFeedback`. The producer's actual FeedbackStore is passed to the
existing `refinery.feedback_collection.collect`, not a fake store or synthetic
feedback-proof callback. A real SQLite receipt binds the exact selected normalized
event digest, authenticated actor, immutable output revision/content and dependency
commit. Collection idempotence, cross-batch conflicts and family locks remain the
existing implementation rather than a second collector.

Task authority, retained delivery/readback, original source material and the three
separate retention/collection/training authorities remain explicitly synthetic.
The integration does not claim that task source bytes were authenticated by Slack
or that collection/training consent is a provider event. Use-time verification is
exercised directly through `RetainedFeedbackUse`, without model/provider dispatch.

Fresh optimized subprocesses reopen actual stores, retry the signed event and
reimport the same snapshot. A signed conflicting body is retained as a Tether
conflict; reimport quarantines it under the existing cross-batch
`conflicting_feedback_id` reason. Prior snapshots remain immutable historical
records, and use-time checks reject their now-conflicted evidence. Family
rebinding and changed output revision reject. Retention revocation denies ingestion,
including duplicate transport delivery; collection and use revocations are
independently required to deny later import/use of already-retained historical
records. This is not a global retention-to-training revocation service.

## Effects and freshness limits

Tests deny socket creation and observe unchanged provider-request and task-admission
ledgers. During collection/use verification, Popen accepts only the loader's exact
read-only `git rev-parse HEAD` command; no training, provider or shell command is
allowed. Fresh-process launches are explicit test setup, not product dispatch.
The authentication-comparison mutation reruns the independent forgery regression
and must fail it. A separate real ingest/collect under the same fault confirms
a contaminated example is persisted. Modifying pinned adapter
bytes rejects before lineage persistence.

Tether supports only historical create commands. Provider edits/deletes are
rejected, so retained `state=active` is not evidence the Slack message remains
current. Automatic collection/training activation is **not qualified**. Pending
work includes mutable feedback revisions/deletions, current-message freshness,
production material/consent authorities and live component qualification. Issue #15
remains open. No live accounts, real keys, provider calls, deployment, scheduled
jobs or permission changes are part of this candidate.

## Bounded reproduction

Prepare an external checkout of exact Tether commit
`040b71f2213a34f34e6b6a40124ed1164b93ce30`, then run:

`RUNSPINDLE_TETHER_SOURCE=/path/to/exact/tether python3 -m unittest discover -s tests -p test_signed_feedback_collection.py -v`

Run the full normal and optimized unittest discovery suites and `tools/validate.py`
with the same environment and existing pinned quality tools. Python 3.12.14 is the
local interpreter; isolated runner image qualification remains separate. Temporary
SQLite stores are removed by unittest cleanup; subprocess fixture stores close in
finally blocks. Source pins and separate passed/failed/not-run outcomes are in
`docs/signed-feedback-consumption-evidence.json` when available.
