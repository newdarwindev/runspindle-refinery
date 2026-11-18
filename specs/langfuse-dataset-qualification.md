# Pinned ephemeral dataset client (source-only preparation)

`tools/langfuse_dataset_check.py CONFIG REPORT` is an opt-in synthetic client.
It starts no services, creates no projects/datasets/credentials and cannot prove
server identity from an HTTP response. Actual backend execution remains pending
independent source/orchestration review and fleet admission. No capacity, timeout
or runner admission limit is relaxed by this source.

## Runner contract

The runner must create a fresh ephemeral synthetic project and exactly one empty
existing dataset, with metadata `runspindle_dataset_split: "train"`. The signed
local fixture deterministically materializes the train split; mismatches deny
before export. No trace is required. Supply exact current Tether source commit
`1e8d2091c55dfad47fdc64ec2e73fcd946da9921` via the existing
`RUNSPINDLE_TETHER_SOURCE` protocol, plus real Git. Source checks remain enforced.

Private config JSON has exactly: `schema: 1`, `origin`, `authorization_file`,
`project_id`, `dataset_id`, `dataset_name`, `dataset_split: "train"`, `ca_file`,
`ca_sha256`, and `backend`. Paths are canonical absolute owned regular files;
config/auth are mode 0600, no symlinks/hardlinks/special files. Duplicate JSON
keys, unexpected fields, altered hashes and non-IP-loopback HTTPS origins deny.
CA bytes must match their digest and parse in the existing loopback-only TLS
transport. Authorization stays private and never enters reports/errors.

The exact backend descriptor is version `4.82.0`, manifest SHA-256
`ea4f29781be662f8c0b15c5c5a004f5352d3b769b8f143fa6556dd20a717ce76`, config SHA-256
`b7123f7d32f06e4a01c42795ebc331abb2493ccd09b90bcf9e9ea7ea54c612b2`.
This supplied descriptor is an expectation, NOT observed image provenance. The
outer runner must independently retain pinned image/config, isolated namespace,
service lifecycle and cleanup receipts. A protocol-compatible fake could produce
this client receipt; the receipt explicitly requires those outer proofs.

Launch the CLI in a dedicated new process session and enforce the external
110-second process-group supervisor within the existing 120-second lane budget.
The client verifies it is session/process-group leader, but cannot prove that an
external timer exists. The HTTP workflow is bounded to 90 seconds, each call at
most 10 seconds, at most 1 POST and 32 GETs. Read-only reconciliation has at most 15
attempts with 0.2-second pauses. These bounds are not performance qualification.

## Workflow and receipts

Real signed synthetic materialization flows through Export, DatasetProjection
and DatasetHTTP. The observer forwards exact requests unchanged and injects one
named lost acknowledgement ONLY after an actual 200 POST response with matching
item ID. The committed local attempt stays unresolved. The same Store is opened
again; bounded exact readback settles without another POST. This is a local
Store reopen, not a backend restart or a fresh-process recovery claim.

After settlement, current export grant denial, signed-source invalidation,
explicit registry withdrawal and sticky local tombstone are checked without
further HTTP. Registry withdrawal has its own exact local oracle; it does not
claim an independently repeated dispatch after the source is already invalid.
No archive/delete/remote erasure is attempted or inferred. The prior confirmed
remote content may remain after local withdrawal.

The report contains hashes and counts, exact backend expectation and CA digest,
source hashes (all refinery modules, transitive test fixtures, shared contract,
Tether pin and reused private-file reader), verified Tether source hashes/commit,
and runtime version/machine/executable digest. Request/response bodies and
endpoint identities appear only as hashes. No auth, raw fixture bytes or private
paths enter the report. A successful report is created exclusively mode 0600;
existing files are never overwritten. Fixed-stage failure diagnostics include
only allowlisted stage/class/status/counts and client-source hash, with an
explicit failed classification. Failures never emit success receipts.

Source tests use only synthetic mocked protocol peers and invalid isolated CLI
invocations. They do not run Langfuse, start real dependencies, send real user
content or demonstrate real-account compatibility. The earlier composed
loopback-TLS suite separately verifies the unchanged concrete transport path.

The fixture outbox exists only for this bounded client invocation and its local
reopen challenge. This is not a cross-invocation recovery tool. The runner must
not blindly relaunch this client against the same uncertain backend state; abort
and retain failure evidence, then clean up the entire disposable namespace under
its approved lifecycle. Any new campaign requires a separately fresh empty
project/dataset. Ordinary production outbox persistence semantics are unchanged.
