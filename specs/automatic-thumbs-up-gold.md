# Automatic gold labels from attributable thumbs-up

An authenticated thumbs-up is sufficient feedback to label the exact retained
output revision's bytes as gold. No written correction, reviewer explanation or
bespoke task-type positive whitelist is required. This is **label promotion**, not
model promotion, deployment, an evaluation score or permission to collect/train.

The explicit `Feedback.kind="thumbs_up"` uses the component-owned rule
`authenticated-thumbs-up.v1`. Its immutable event observation must be independently
verified, including actor, target revision/hash and reaction provenance. Merely
setting an actor string or changing arbitrary `positive` feedback to this kind is
not authentication. Generic positive/correction judgments keep existing task-rule
requirements. The retained-feedback collector maps the authenticated Tether
`judgment="thumbs_up"` to the automatic rule; a selected bespoke rule cannot veto
or silently broaden this policy.

Missing or changed source/output, incomplete context, unverifiable actor, deleted
or unavailable targets still cannot become examples. Genuine negative feedback or
different correction bytes are not erased. A thumbs-up and existing approved
positive judgment for identical bytes coalesce under the automatic rule while
retaining each event/actor. Duplicate immutable deliveries are deduplicated; a
conflicting reused event ID remains quarantined. Related revisions retain their
family and locked split.

## Surface inventory

| Surface | Capture boundary |
| --- | --- |
| Retained Tether event store | Independent authenticated event/target authority and explicit selection; supports no invented actor identity. |
| Signed Slack reactions | Integration depends on the separately reviewed Tether thumbs-up intake and exact updated dependency pin. Reaction removal must invalidate retained use. |
| Signed Slack thumbs-up text | Only supported where the reviewed Tether adapter explicitly admits the exact signed signal. Not inferred from arbitrary prose. |
| ChatGPT, SMS, email, Teams and other raw reactions | No capture adapter is implemented here. This universal downstream label policy does not claim those reactions have been collected or authenticated. |

Existing collection/training batch consent, source fences, retention revocation,
use-time authorization and privacy rules remain mandatory. Historical snapshots
and dependency receipts are not rewritten or retroactively authorized. New source
support must provide the same exact immutable observation contract before this
policy can label its output.
