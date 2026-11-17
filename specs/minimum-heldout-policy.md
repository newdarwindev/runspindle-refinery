# Minimum held-out sample policy

This partial functional step for #2 and #6 restores the selected SRC-012 helper's
explicit minimum-evaluation-sample gate in the durable qualification path. It does
not qualify a live provider, production approval issuer or statistical quality.

`SamplePolicy` is an opt-in immutable stored policy type extending the existing
quality, improvement, cost, latency and target constraints with
`min_heldout_cases`. It requires an exact positive integer no greater than the
existing 1000-record lineage bound. The default is one. Existing `Policy` keeps
its original fields, encoded bytes, content hashes and behavior; old approvals and
stored objects are not rewritten or silently upgraded.

`Registry.qualify` admits only these two known exact policy types and validates
them before reading evaluation state. For `SamplePolicy`, it checks the unique,
validated `Report.heldout_cases` denominator against the minimum before writing
policy/candidate objects. Baseline calls, preparation, reflection and retries do
not create additional held-out cases. Denial leaves registry objects, activations
and serving state unchanged and performs no provider call.

The new type name and minimum are part of the existing content-addressed policy
bytes. Different sample requirements therefore produce different policy/candidate
identities even for the same evaluated artifact. Existing approval checks bind the
exact policy hash; approval for a looser policy cannot authorize a candidate
qualified under another policy. Qualification is not approval or serving load.

Example with already committed synthetic evaluation `evaluation`:

```python
from refinery.serving import Registry, SamplePolicy
from refinery.storage import VERSION

policy = SamplePolicy(
    "local", 900000, 50000, 20000, 20000, min_heldout_cases=30
)
candidate = Registry(store).qualify(VERSION, "evaluation", policy)
```

Thirty exact-match synthetic held-out cases are still only fixture evidence, not
proof of generalization, unbiased sampling or acceptable real-world risk. Related
family split/lineage, artifact identity, unknown safety/cost denial, exact score
arithmetic and separate approval gates remain unchanged. The count is observed
from trusted persisted evaluation evidence, not from a caller-declared sample flag.
A plausible forged trusted store is not authenticated by this policy.

Tests exercise below/at-boundary qualification, no-write denial, retry/preparation
exclusion, policy-hash approval mismatch, invalid/foreign policies, byte-exact legacy
codec preservation, new-type roundtrip and fresh optimized-process qualification
with provider/load/network denied. Deliberately removing the minimum guard permits
a forbidden candidate and fails the unchanged negative assertion.

```sh
python -m unittest discover -s tests -p test_sample_policy.py -v
python -O -m unittest discover -s tests -p test_sample_policy.py -v
```

Full validation retains the existing exact Tether dependency and pinned quality
requirements. #2/#6 remain open for their broader integration/source/qualification
obligations. No installation, schema migration, service, new credentials, network
permission, live evaluation or production activation is introduced.
