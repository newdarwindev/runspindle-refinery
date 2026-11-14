# Reviewed paper in the hygiene validation contract

This integration combines hygiene PR #18 at `ee1274fabcce6b6bc7659ac667609608ccf8d8c3` with merged SRC-013 paper PR #24 at `80a87f4569f1dfef14b141e141a89f30a9aaf7cd`. The reviewed paper content commit is `bfa8f760c1d56ec1c2ce07ea49e4d37ce4c01e5b`.

The hygiene inventory declares `check-paper` as a required command and the component partition assigns it exactly once. It is no longer an indirect subprocess of scaffold validation. The new negative tests reject missing or duplicate partition assignments. The six existing paper tests retain closure and tamper checks under normal and optimized Python.

The TeX source, accepted PDF, all eight figures, license, notices, disposition, toolchain lock and original native build evidence are unchanged. Only import ordering in the CLI wrapper and whitespace in the checker changed for the existing isolated Ruff gate; the wrapper command and environment are unchanged. Its end-to-end execution remains unrun. This integration performs no additional native compilation.

The workflow, qualified runner-image gate, Tether dependency pin, pinned quality requirements and quality baseline remain unchanged. Fresh local integration receipts are separate from the original paper build evidence. Local aggregate validation is not qualified-runner CI, deployment, provider use or live acceptance. The umbrella mirror remains a separate dependency and issue #3 remains open.

Run `python3 tools/validate.py` in the prepared validation environment, then `python3 -O -m unittest discover -s tests`. The pinned Tether checkout must be supplied as `RUNSPINDLE_TETHER_SOURCE`. No installation or unpinned dependency fallback is part of these commands.
