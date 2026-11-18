# Shared dataset materialization v1

Identity encoding is the feedback contract's exact ASCII-escaped canonical JSON
(sorted keys, compact separators, finite values, UTF-8 encoding, no normalization).
The fixture includes non-ASCII input/output to make byte behavior testable.

Immutable item identity binds schema version, opaque dataset name, authoritative
feedback occurrence identity SHA, canonical input SHA, **original artifact bytes**
SHA, source-family SHA and split (`train` or `heldout`). `item_id` is SHA256 of that
canonical binding, excluding itself. Input is finite JSON; output is exact UTF-8
artifact bytes interpreted as a string, not reserialized JSON. Each is at most64KiB.

Authentication, current gold/use permission, source-to-input/family linkage and
materialization authorization are REQUIRED BEFORE content retention. A gold score
alone does not authorize copying data. The complete descriptor plus exact hashes
must be supplied to an independent materialization authority. Output bytes must
match the authenticated target artifact hash. Source-family identity is supplied
by a trusted lineage authority, not inferred from equal output text.

One family, input hash and source occurrence cannot cross train/heldout within a
local dataset registry. Semantic near-duplicate detection is not implied. Every
content read/use rechecks current source and local withdrawal authority, exact
binding and purpose: train for optimization; heldout only for evaluation. Persisted
materialization receipts are history, not evergreen permission.

Default remote projection contains only descriptors/hashes. Uploading input/output
requires distinct current authorization for the exact example and destination.
Remote dataset status never grants local use. Source or consent revocation excludes
local reads immediately on the next guarded use. Retained bytes are not claimed
erased; approved deletion and remote archival require separately verified outcomes.

No dataset export, training, evaluation, model promotion or remote cleanup is
implied by computing this binding. No Temporal dependency is introduced.
