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
| Signed Slack reactions | Pinned merged Tether `1e8d2091c55dfad47fdc64ec2e73fcd946da9921`: signed `thumbsup` and `+1` reactions. Reaction removal and signed exact-target edit/delete fences deny retained use. Skin-tone variants currently rejected; not silently normalized. |
| Signed Slack thumbs-up text | The reviewed adapter recognizes exact signed thumbs-up text as `thumbs_up`; this PR’s signed end-to-end qualification covers reactions, not live message subscription. Not inferred from arbitrary prose. |
| ChatGPT, SMS, email, Teams and other raw reactions | No capture adapter is implemented here. This universal downstream label policy does not claim those reactions have been collected or authenticated. |

Existing collection/training batch consent, source fences, retention revocation,
use-time authorization and privacy rules remain mandatory. Historical snapshots
and dependency receipts are not rewritten or retroactively authorized. New source
support must provide the same exact immutable observation contract before this
policy can label its output.

## Exact dependency transition and retained history

The active manifest pins the merged Tether source above and every loaded module's
bytes. `dependencies/tether-feedback-ae5ac216.json` preserves the prior manifest
unchanged. Existing historical receipts remain immutable. Old c3fbfb9/ae5ac216
receipts can be explicitly revalidated into the new pin only with fresh exact-batch
collection/training approval and unchanged selection/material/content. No silent
migration or inherited use approval occurs.

The dependency is original code in the owner's Tether repository, loaded from a
verified external checkout; no implementation is vendored or relicensed here.
Only the manifest-listed modules are admitted. This transition also includes
upstream changes to those modules since the old pin; existing collection/use and
revalidation regressions run against the new exact checkout.

Signed synthetic tests traverse actual HMAC verification, retained ProviderJournal,
feedback store, Refinery collection and use validation. They cover automatic gold
without any task rules, duplicate receipt, aliases, forged signature, wrong actor
or exact target, reaction removal, bot-output edit, target deletion before intake,
mutation during approval rollback and separate collection/training/use/material
denial. A target fence is a negative observation, not proof of current freshness:
independent material/source verification and use-time fences remain mandatory.
No live listener, subscription, external capture or model execution was run.
