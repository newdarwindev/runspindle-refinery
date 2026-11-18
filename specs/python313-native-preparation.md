# Native Python 3.13 preparation evidence

The [receipt](../docs/python313-native-preparation.json) records four native
installations of the [existing exact recipe](../offline/python313/recipe.json):
two fresh directories in each of two fresh no-NIC ARM64 guests. The original
Python 3.14 lane and evidence remain unchanged.

All four installations resolved 76 distributions using Python 3.13.13 and uv
0.13.8, without ignoring dependencies or Python requirements. All wheel hashes,
RECORDs, architecture tags and Requires-Python declarations passed the existing
strict auditor. In particular, GEPA 0.0.22's declared Python range includes this
interpreter, unlike the historical 3.14 lane.

The two complete auditor reports match. All four inventories match every path,
type, mode and file hash, including 11,877 files and 14,480 total paths. The
[candidate manifest](../offline/python313/candidate-dependencies.json) and
[full mode inventory](../offline/python313/candidate-file-modes.json) are evidence,
not active runtime inputs. No prepared bytes or modes were normalized. Both
native resolver runs per guest retained the expected hardlink-to-copy warning;
the recipe was not changed to suppress it.

Guests completed in 177.725 and 177.965 seconds, under unchanged bounds, with
clean shutdown and unchanged base image. No matching guest remained afterward.
Private raw receipts are retained by hash. The repeatability claim uses the same
sealed UDF wheelhouse, including filesystem metadata; reconstruction from loose
wheels and artifact distribution remain separate requirements.

This establishes candidate preparation only. It does not qualify actual GEPA
reflection, DSPy runtime behavior, live providers, deployment or promotion. No
framework code was imported for execution and no provider/model quota was used.
Explicit reviewed strategy/toolchain admission and actual bounded tests are still
required before this candidate can become an execution authority.
