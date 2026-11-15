# Opt-in immutable-container x64 smoke

Status: source-only proposal. No image, isolated run or framework result is
qualified by this change. The ARM64 runners, canonical pins and issue #16 gates
are unchanged. Construction of a contract grants no execution authority.

The separately prepared, independently byte-audited x64 candidate is pinned by
`tools/dspy_x64_container_smoke.py`. This is a new strict extraction and two actual
offline preparations, not metadata repair of an incompatible older tree. It
retains the existing 76 locked versions. Publisher authentication, host trust and
authorization remain separate requirements.

The opt-in driver verifies a SHA256-bound contract, complete source/runtime/deps
trees, exact candidate inventory, native-library hashes and synthetic fixture
before importing the provider. The future smoke executes one real DSPy Predict
call with one deterministic synthetic completion. It must report its actual
outcome. It is not BootstrapFewShot, restart qualification, live-provider quality,
serving or full product execution. The old staged x64 scripts are not imported.

An independently reviewed preparation must produce a COPY-only `FROM scratch`
image from the committed source, strict runtime and deps, pinned native libraries,
candidate and contract. No RUN, package installer, registry pull, resolver repair,
host runtime bind mount or network/security alteration is part of this proposal.
Regular input bytes/modes and opaque contained runtime links are preserved;
snapshot directories have an explicit searchable 0755 policy and root ownership.
Original shared hardlinks remain untouched. The image must be independently
exported and byte-compared to the complete reviewed inventory before its exact
immutable image ID is eligible for an execution approval request. A read-only
host bind mount alone does not seal writable aliases.

Static DT_NEEDED enumeration must include dependencies of every runtime and
dependency ELF, recursively resolved against bundled libraries and pinned host
DSOs. It does not establish arbitrary dynamic-import/dlopen completeness. Missing
native imports fail the actual run; no on-the-fly install or alternative setup.

`create_spec` is an inert exact Engine request. Before start, `check_observation`
requires the image, source/contract labels, non-root identity, exact environment,
no mounts, network-none, read-only root, no capabilities, no-new-privileges,
private namespaces and fixed cgroup/process/memory/CPU/scratch/file limits. No
restart, auto-removal or rotating Docker logs is permitted. Python is namespace
PID 1; no unpinned daemon-injected init binary is requested.

The future reviewed host launcher must enforce a 120-second wall bound and stop
on output exceeding 1 MiB per stream, then fully drain and preserve all produced
logs, including any overrun and shutdown output. It must retain stopped container
and immutable image evidence. It must observe the actual container cgroup becoming
unpopulated after stop; leader exit alone is insufficient. `check_result` rejects
nonzero/OOM, timeout/output stops, incomplete stream capture and missing/positive
cgroup-population evidence. Its inputs must come from actual observations, not
invented booleans. The host launcher implementation and exact engine/image review
are still prerequisites; these pure validators do not perform those operations.

Any access or isolation denial stops that attempt. Do not retry through another
daemon, namespace, security profile, user or package source. The earlier bwrap and
resolver records remain evidence; this proposal cannot authorize a bypass.

`tests/test_dspy_x64_container_contract.py` exercises synthetic configuration and
byte fixtures only. It invokes neither Docker nor the framework. Actual execution
requires separate review of the final source, native closure, exported image ID,
launcher and resource plan, followed by applicable owner authorization.
