# Proposed signed materialized GEPA qualification

This harness is not a native execution receipt. Source-only tests exercise the
oracles, command construction, cleanup and actual signed local stores. They do
not substitute for original DSPy/GEPA execution. Runtime source remains unchanged.

## Separate bounded lane

After source review and prepared-input verification, the proposed command is:

```
python3 tools/offline_gepa.py --suite materialized --prepared /absolute/deps \
  --tether /absolute/exact-tether-checkout --git /absolute/real-arm64-git \
  --report /absolute/new-report.json
```

The existing exact Python 3.13.13 image, dependency adoption, network-none,
read-only container and 120-second execution limit are retained. The new suite
uses two sequential children bounded at 50 seconds each, leaving only the existing
outer budget for preparation and cleanup. This is a declared bound, not a measured
performance claim. A timeout fails qualification; no automatic deadline expansion.
Reflection/durable suites reject the new input arguments and retain their behavior.

Only this suite mounts the explicit canonical, nonsymlink Tether root at `/tether`
and one regular mode-0755 Git executable at `/usr/bin/git`, both read-only. Git must
hash to `54af380ba6ca1b36305358e99427a31ae4b0af5dc5cb6d0198c6f2f16e97651d`.
It is the separately verified Debian ARM64 binary, not a fake Git shim. The
container image supplies its existing libraries; PATH is unchanged and no
LD_LIBRARY_PATH, installation, download or fallback is introduced. The ordinary
TetherDependency constructor verifies real Git HEAD and all current pinned source
bytes at `1e8d2091c55dfad47fdc64ec2e73fcd946da9921`. Outer artifact sealing remains
an independent native-run prerequisite.

## Actual entrypoints and strict observations

The coordinator creates the existing signed synthetic Tether reaction fixture,
materializes its exact train item with an explicit synthetic grant, and keeps the
original files alive while children run. It does not invoke a framework. The
compile child reopens those actual files and calls the real durable Session and
original GEPA.compile, Predict and ScriptedProvider methods. All original
five-effect reflection oracles remain mandatory. Optimization and reflection use
the same optimization manifest but separate current purpose authorization checks;
a stored permission reference is not treated as a grant.

The fresh recovery process blocks DSPy/GEPA imports and provider entries before
component reconstruction. It must retain exact candidate/configuration/ownership,
manifest, permission reference, physical authority storage and logical checkpoint
hashes with zero dispatches/imports/network attempts. It then withdraws the item
from the actual registry and must deny recovery, preserving budget/checkpoint
bytes and unresolved accounting. All content, grants and signing keys are explicitly
synthetic. Signed ingress verification and stores are real component code; current
authority callbacks are local synthetic fixtures, not live account approval.

Network-attempt denial covers fixture preparation and child imports/execution.
The VM/container no-network boundary is additional, not a substitute for attempt
counters. Original framework calls are transparently observed, never replaced by
fake implementations in a native run.

## Identity and reporting

Reports bind all Refinery modules, used signed fixture sources, launcher/harness,
Tether manifest and exact Git/Tether input hashes. Physical owner bindings include
actual local path/device/inode hashes. They must match between compilation and
recovery of the SAME files. Independent runs create different files and may have
different physical identity hashes. Retain full reports; never strip or normalize
those fields to manufacture cross-run equality. Compare each run against its own
strict oracle and publish any cross-run invariant comparison explicitly.

No heldout evaluation, live provider, production deployment or parent-issue
closure is claimed. A native receipt must separately record real outcomes,
failures, cleanup and exact source/inputs before this lane can be called qualified.
