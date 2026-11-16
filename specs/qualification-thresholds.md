# Exact qualification thresholds

The existing synthetic exact-match evaluator applies ppm thresholds to integer
correct counts and a locked held-out denominator, then persists scores as floats.
Qualification now decodes only canonical floats exactly equal to an integer
`k / N`, where `N` is the number of valid, unique retained held-out identities
and remains bounded by the existing 1,000-record limit. It compares integer
cross-products for quality and signed improvement. No epsilon, permissive
rounding, decimal approximation or threshold change is used.

For example, one correct baseline and three correct candidate cases out of five
meet a 400,000 ppm improvement threshold. Subtracting the stored floats produces
399999.99999999994 ppm and previously rejected that valid boundary.

Report fields, canonical bytes, digests and storage schema are unchanged.
Preparation calls and retry attempts do not enter the denominator. Invalid,
nonfinite, non-float and off-grid scores, invalid/duplicate identities and
out-of-bound denominators fail before candidate creation.

This is an arithmetic correction under the existing immutable-report trust
model. It does not reconstruct correct counts from independent observations,
authenticate report provenance, change approval or promote/load a candidate.
Production serving, provider and DSPy qualification remain separate.

Run the focused tests with:
`python3 -B -m unittest discover -s tests -p test_qualification_thresholds.py`.
Repeat with `python3 -B -O`. They use real component SQLite and the existing
ScriptedProvider with synthetic feedback authorities, never a live provider or
serving activation. The mutation restores floating comparisons and must fail the
independent exact-boundary expectation.
