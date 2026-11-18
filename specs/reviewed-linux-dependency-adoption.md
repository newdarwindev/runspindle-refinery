# Explicit Linux-native dependency adoption proposal

This reviewed-source proposal changes the active offline qualification declaration
from the historical inventory to the exact independently prepared PR62 candidate.
It does not establish framework qualification, live-provider capability, production
readiness or promotion authority. Actual DSPy execution remains **not run** in this
change and must wait for source review and the separately authorized isolated run.

## Preserved history, explicit new selection

The original `offline/dependencies.json` is preserved byte-for-byte as
`offline/historical/dependencies-before-linux-native-20261118.json`, SHA-256
`087981ca63d9ca13d8a9a1a33072f553885a52c089b0b419dd31c08c6b27de52`.
Historical test receipts are unchanged and refer to that earlier closure. Their
results do not transfer to the newly selected dependency bytes.

The new active file is exactly the retained Linux-native candidate file, SHA-256
`84477d54127b86bc47b5d2323fd40715ec1b67d4a260e174ad9c60161a313a8c`.
Its canonical-JSON digest is separately
`2f439085e18c57e109144d40bb8eab300597d88de6127fad13afc218590fdc1f`.
This is a new closure, not recovery of an original installer identity. The 76
retained uv metadata files and corrected scripts are not stripped or normalized.
Licenses, notices, package identities, image, Python, lock and toolchain remain
unchanged. Candidate delta tests compare with the explicitly pinned historical
file, rather than treating the new active manifest as its own historical baseline.

`offline/dependency-adoption.json` binds exact active/historical/candidate files,
recipe, wheelhouse receipt, preparation receipt, lock, toolchain and notices. A
fixed receipt digest in launcher source prevents an edited receipt from approving
its own replacement hashes. Any update therefore requires a visible source change
and review; no caller-selected manifest or permissive fallback exists. The
launcher captures each bounded regular source file through no-follow descriptors,
verifies captured bytes and compares the actual prepared inventory before Docker.
Original network isolation, resource limits and cleanup checks remain intact.

All existing execution/configuration consumers continue to use the same active
manifest. Optimizer configuration digests consequently bind the newly adopted
bytes, not the historical manifest. The preparation auditor still returns an
unreviewed candidate and never grants execution authority: equality with the active
manifest is only an integrity comparison.

## Artifact and execution limits

Reproducing preparation requires the same retained read-only UDF wheelhouse,
SHA-256 `241dc8e276f8c3f3e5395f9b853edcd13e690439f1a50bde5f036e1937a69671`.
It is not distributed by this change, and reconstruction of the UDF is not verified.
The launcher checks the resulting full installed-byte inventory; it does not claim
to mount or reverify that preparation artifact during execution. A missing artifact
blocks preparation reproduction, not permission to invent substitute provenance.

The operator must keep source and prepared trees immutable through admission and
execution. Descriptor-relative source capture and PR63 file-type preflight do not
provide exclusion against a concurrent host writer. These are trusted local
source/runtime gates, not an authenticated external approval service.

## Verification and remaining qualification

Synthetic tests observe zero subprocess calls for missing/changed source files,
receipt self-authorization, wrong image, altered installed inventory, historical
rollback and symlink aliases. A deliberately substituted receipt pin makes the
independent rejection oracle fail. A fake subprocess positive test verifies
network-disabled dispatch/cleanup arguments without Docker or dependency imports.

After independent source review, use the existing bounded command on the isolated
Linux ARM64 lane with the exact pinned closure:

```sh
python3 tools/offline_dspy.py --suite optimizer --prepared /canonical/prepared/path --report local/optimizer-report.json
```

Record the exact source revision, new adoption receipt, configuration/candidate
and report hashes, forbidden-call observations and cleanup. Repeat actual framework
qualification and keep retry/cancellation/budget/holdout/recovery coverage explicit.
Do not close issue #16 merely for changing this declaration or passing source tests.
