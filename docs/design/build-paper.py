"""Bounded local paper build. Existing Debian TeX Live only; no installers."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--previous", type=Path)
args = parser.parse_args()
source = Path(__file__).resolve().parent
out = args.output.resolve()
if out.exists():
    raise AssertionError("Choose a fresh directory; existing logs are preserved")
lock = json.loads((source / "toolchain-lock.json").read_text())
for item in lock["files"]:
    if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() != item["sha256"]:
        raise AssertionError(item["path"])
out.mkdir(parents=True, exist_ok=False)
if args.previous:
    for suffix in ("aux", "out"):
        f = args.previous.resolve() / ("workflow-refinery." + suffix)
        if f.exists():
            shutil.copyfile(f, out / f.name)
tex = (source / "workflow-refinery.tex").read_text()
for forbidden in (
    "\\write18",
    "\\openin",
    "\\openout",
    "\\read",
    "\\input",
    "\\include{",
    "\\immediate",
    "\\directlua",
    "\\special",
    "\\catcode",
):
    if forbidden in tex:
        raise AssertionError(forbidden)
if any((p.is_symlink() for p in source.rglob("*"))):
    raise AssertionError()
env = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "SOURCE_DATE_EPOCH": "1794614400",
    "FORCE_SOURCE_DATE": "1",
    "TEXMFHOME": "/tmp/empty",
    "TEXMFLOCAL": "/tmp/empty",
    "TEXMFCONFIG": "/tmp/empty",
    "TEXMFVAR": "/out/texmf-var",
    "TEXMFOUTPUT": "/out",
    "TEXMFCACHE": "/out/cache",
    "TEXMF": "{/usr/share/texmf,/usr/share/texlive/texmf-dist,/var/lib/texmf}",
    "TEXFORMATS": "/var/lib/texmf/web2c/pdftex",
    "TEXINPUTS": ".:/usr/share/texlive/texmf-dist/tex//:/usr/share/texmf/tex//",
    "openin_any": "p",
    "openout_any": "p",
    "shell_escape": "0",
    "MKTEXFMT": "0",
    "MKTEXTFM": "0",
    "MKTEXPK": "0",
    "MKTEXMF": "0",
    "MKTEXTEX": "0",
    "MKTEXOFM": "0",
}
cmd = [
    "/usr/bin/timeout",
    "--kill-after=5",
    "120",
    "/usr/bin/bwrap",
    "--unshare-all",
    "--die-with-parent",
    "--new-session",
    "--ro-bind",
    "/usr",
    "/usr",
    "--ro-bind",
    "/lib",
    "/lib",
    "--ro-bind",
    "/lib64",
    "/lib64",
    "--ro-bind",
    "/etc/texmf",
    "/etc/texmf",
    "--ro-bind",
    "/var/lib/texmf",
    "/var/lib/texmf",
    "--proc",
    "/proc",
    "--dev",
    "/dev",
    "--tmpfs",
    "/tmp",
    "--ro-bind",
    str(source),
    "/src",
    "--bind",
    str(out),
    "/out",
    "--chdir",
    "/src",
    "/usr/bin/pdflatex",
    "-no-shell-escape",
    "-no-parse-first-line",
    "-halt-on-error",
    "-interaction=nonstopmode",
    "-file-line-error",
    "-recorder",
    "-output-directory=/out",
    "workflow-refinery.tex",
]
with (out / "console.txt").open("xb") as log:
    proc = subprocess.run(
        cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=130
    )
print((out / "console.txt").read_text())
print(json.dumps({"exit_code": proc.returncode, "runtime_acceptance": "not_run"}))
sys.exit(proc.returncode)
