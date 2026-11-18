# Sealed GEPA Python 3.13 qualification lane

Status: reviewed-adoption source proposal and actual-framework harness, not a
native execution result. This separate strategy does not modify the Python 3.14
Bootstrap manifest, adoption, launcher or historical receipts. It does not close
issue #16 or establish durable Session, heldout or production acceptance.

The fixed SHA-256 in `refinery/gepa_runtime.py` binds
`offline/python313/gepa-adoption.json`. That receipt binds the exact existing
recipe, 76-distribution dependency inventory, full 14,480-path byte/type/mode
inventory (11,877 files), requirements, exact Python 3.13 image license and its
verified OCI-layer provenance, input provenance and independently
reviewed four-install/two-guest preparation evidence. Both inventory file digests
and canonical JSON digests are checked. Original candidate files remain unchanged. The separately retained 3.13 license
is 13,809 bytes, SHA-256 `78b12c3a81360b357002334f0e70ea0e92eebf7a9b358805c03c48484945f3bb`;
it differs from the preserved 3.14 notice and is not substituted by that notice.
The recipe's image/config, interpreter, uv archive/binary/checksum and sealed UDF
provenance remain the existing reviewed values. Native preparation establishes
this sealed input realization, not arbitrary wheelhouse rebuild/distribution.

`tools/offline_gepa.py` performs full source/inventory checks before Docker. It
accepts only the pinned Linux ARM64 image/config, never pulls, never prepares and
never accepts another manifest. Execution has no network, a read-only root and
input mounts, dropped capabilities, no-new-privileges, two CPUs, 1536 MiB memory,
256 PIDs, a 512 MiB temporary filesystem and a 120-second runtime deadline.
Command output is bounded to 1 MiB. Image inspection and cleanup each have bounded
30-second commands; child kill/reap and container removal/absence checks run on
failure too. A successful exact-name `docker ps -a` query with no IDs is required
to establish absence; daemon errors and nonempty results fail closed, including
after a successful runtime command. The provider spy records entry before calling
the original provider, so throwing calls remain observed. No caller credentials are passed into the container. Report paths
must be new; a failed run never yields a success receipt.

Before framework imports or effects, the internal strategy verifies exact
CPython 3.13.13, Linux/aarch64, interpreter path, source adoption, every prepared
file byte and every directory/file mode. Symlink ancestors, hardlinks, bytecode
and special nodes reject. Framework import origins must be the verified `/deps`
roots. Runtime identity, including adapter/codec/accounting source hashes, enters
the GEPA configuration. Full descriptor-based snapshots detect changes during
reads but are not an atomic filesystem/concurrent-writer proof: supplied source
and prepared directories must remain immutable for the run. Read-only mounts and
both host/guest checks preserve this narrower contract.

The actual harness transparently wraps and calls original `GEPA.compile`,
reflection `Predict.forward` and `ScriptedProvider.complete`. Expected bounded
single-example flow is initial training validation, traced failed training task,
one metered reflection proposal, changed task evaluation and changed validation.
The original optimizer must return the changed instruction produced by reflection;
script inclusion alone cannot satisfy the oracle. The codec and independent
provider observations must agree with all five receipts and exact cumulative
charges. Two isolated disposable SQLite runs must agree, with zero network
attempts. Failure prints bounded synthetic reason/count/hash diagnostics and
remains a failure. No candidate is hand-selected and no fake framework qualifies.

Source tests cover adoption/recipe/manifest alterations before Docker, exact
file and directory modes, aliases/special files, wrong runtime, image/launcher
controls, output/deadline bounds and independent negative oracles. These tests
remain distinct from native actual-framework qualification. Native execution,
independent source review and the full repository gate must be recorded against
the final frozen source before any qualification claim.
