# Explicit retained-feedback revalidation

Bounded child work for Refinery #15/#4. The verified Tether source closure now
pins `ae5ac216b45ca413283e4200c0a4a61054998b58`. It still imports only the existing
13 feedback modules; this does not integrate the optional semantic journal or
raw-message edit adapter. Live Slack authentication and production readiness are
not established by these synthetic fixtures.

## Upgrade contract

Old receipt provenance cannot be rewritten to pretend collection happened under
a new dependency. Old snapshots remain readable, immutable and **denied for use**
by the current-pin use gate. No automatic migration or consent inheritance exists.

A caller may explicitly pass `prior_snapshot_sha256` to `collect` for the one
reviewed transition from `c3fbfb9ff5ed0483e522c93268ced9e40a6cb79d` to the pin above.
It must also supply the normal selections/rules, source adapters and fresh
collection/training consent callbacks and references. The historical snapshot is
not authorization. Each new callback decision binds the current exact batch.

The operation re-observes current Tether evidence and source fences. It requires
identical selections, source/output material, judgments, batch bytes, exclusions,
family membership, seed and locked split. Only the dependency provenance and
fresh consent references can differ. Changed, edited/deleted, fenced, forged,
unsupported or incomplete historical input fails closed. To collect genuinely
changed material, use normal explicit collection; do not label it continuity of
the historical dataset.

Within the existing SQLite transaction, revalidation stores a new current receipt
and snapshot plus a content-addressed `feedback_revalidation.v1` link naming both
snapshot and receipt identities. Original objects are not rewritten. Conflicts,
callback changes or cancellation roll back all new objects. Repeating the same
approved inputs is idempotent. Fresh use approval remains necessary afterward;
no experiment, provider call, promotion or deployment is implied.

This does not authenticate a hostile SQLite store or defend against coherent
rewriting of every historical object. It never authenticates historical consent;
it requires fresh current consent and current independently verified material.
The old/current component databases are not a cross-database atomic transaction;
existing final observation checks and their limits remain unchanged.

## Qualification

Focused normal and optimized suites:

```
RUNSPINDLE_TETHER_SOURCE=<prepared-current-tether> python3 -m unittest discover -s tests -p test_feedback_revalidation.py
RUNSPINDLE_TETHER_SOURCE=<prepared-current-tether> python3 -O -m unittest discover -s tests -p test_feedback_revalidation.py
```

Actual old-source generation, current revalidation and fresh-process repeat:

```
python3 tools/qualify_feedback_revalidation.py \
  --previous-refinery <clean-f8a3f4199994f65ee42bbf2d9cfcae0494dae03d> \
  --previous-tether <clean-c3fbfb9ff5ed0483e522c93268ced9e40a6cb79d> \
  --current-tether <clean-ae5ac216b45ca413283e4200c0a4a61054998b58>
```

The command uses actual versioned code and SQLite in separate bounded processes,
synthetic signed feedback/material/approval fixtures, default-denied socket use
and a temporary state directory. It neither downloads dependencies nor changes
existing stores. This is process recovery evidence, not storage power-loss proof.
