# Actual-framework integrity and mutation proposal

This source-only campaign extends #16 verification; it is not native execution
or full issue acceptance evidence. Use the existing reviewed lane's
`--suite integrity --report <new.json>`. The adopted dependency/runtime closure,
no-network isolation, synthetic-only fixtures and 120-second outer deadline remain
unchanged. No production guard is changed or provider/model quota used.

Five disposable-store cases exercise three challenges:

- **Locked candidate:** compile through genuine BootstrapFewShot and Session,
  alter the resulting candidate bytes, then require exact digest rejection before
  any new provider call. Persisted budget and attempt bytes must remain identical.
- **Caller result identity:** real Session.compile captures a genuine framework
  result, then a precommit checkpoint deliberately mutates the caller's candidate.
  The protected case must deny and roll back its candidate object/claim update,
  retaining the one already-settled call. With only `verify_result_identity`
  deliberately disabled, the outward corrupted bytes must differ from the original
  persisted result. An independent rollback/effect oracle must fail specifically
  on this divergence, not on an import, syntax error or missing framework.
- **Held-out boundary:** the same adversarial routing challenge sends an actual
  framework optimization invocation toward one locked held-out example. The
  original invoke/predict path is retained. With its normal allowlist it must
  deny before any provider effect. With only held-out admission deliberately
  injected into that allowlist, the original provider observer must see exactly
  one held-out request and charged call. The independent no-leak oracle must fail.
  The fixture requires held-out gold to differ from every training gold and uses
  the original one-call budget; its wrong trace and denied retry must yield no
  candidate. This is containment for this bounded challenge, not a claim that
  removing the guard universally prevents contaminated candidates. No harness
  aborts or erases a returned candidate: any candidate fails the oracle. This is
  deliberately broken synthetic execution, not a permitted optimizer mode.

Transparent compile/provider wrappers invoke the originals. All mutation patches
are bounded to a case and restored afterward; stores are distinct and disposable.
The exported report contains digests, synthetic IDs, counts and exact observations,
not candidate payloads or private logs. Deliberately corrupted results never enter
serving or promotion. Oracle source tests are explicitly synthetic and cannot
replace native execution. Existing component mutants and historical evidence remain
unchanged; this campaign adds actual-framework traversal and independent effects
when its exact-source runtime receipt eventually passes.

Actual reflection remains unsupported by this fixed Bootstrap adapter. The #15
authenticated external-feedback dependency and #9 live-provider acceptance remain
separate from synthetic native optimizer qualification.
