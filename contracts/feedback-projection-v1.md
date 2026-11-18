# Shared feedback projection v1

Canonical JSON: sorted keys, compact separators, UTF-8 encoding, finite JSON values.
Strings use JSON ASCII escaping (`ensure_ascii=True`), including surrogate pairs
for non-BMP code points; JSON booleans/literals are lowercase. No Unicode
normalization is performed. Cross-language implementations must match the fixture.
Identity SHA256 covers `{namespace, feedback_id, actor_id, target}`. Target is the
exact Tether target schema including task/artifact revisions, artifact/payload
hashes and message/delivery/workspace/channel/thread identity. Content hashes alone
are NOT identity; identical output in different occurrences/actors stays distinct.

A verified thumbs-up is sufficient gold feedback without further adjudication.
Authentication and retention occur upstream. A copied JSON judgment is not proof.
The local authority additionally governs collection, use, export and current
output validity. Gold is human positive feedback, not correctness or deployment.

Projection fields are defined by `refinery.feedback_contract.projection` and the
shared fixture. Only opaque hashes, monotonically increasing local revision,
state and boolean decisions leave the local boundary. `projection_id` is SHA256
of the entire canonical projection excluding that field. Revision is local to an
identity; duplicate unchanged observations do not increment it. Revocation is a
permanent tombstone for that feedback identity; delayed/replayed positive events
cannot resurrect it. New policy/use decisions may revise active eligibility, not
undo a source revocation. A separate new source lifecycle needs a new identity.

Remote score IDs use projection_id, not a mutable identity-wide overwrite: late
old deliveries cannot overwrite a newer immutable revision. Remote displays must
resolve highest revision and revocations; remote data never grants local use.
A remote stale positive is possible while offline, therefore consumers must ask
local current authority before training. No cross-service atomicity is claimed.
Export permission is checked again before transmission; revocation immediately
blocks local use. Remote cleanup may itself require scoped authorization, and its
pending state must remain visible. No deletion/cleanup success is inferred.

Initial Langfuse projection is opaque feedback score/metadata only. It does not
upload prompt/output bodies or materialize dataset examples. Dataset publication,
actual optimization, model promotion and remote cleanup are separate stages.
No Temporal dependency is needed for this bounded durable cursor/outbox protocol.
