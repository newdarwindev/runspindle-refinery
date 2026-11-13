"""Validate inert reference bytes and syntax without importing or executing them."""
import ast, hashlib, json, pathlib
root=pathlib.Path(__file__).resolve().parents[1]
items=json.loads((root/'docs/reference/manifest.json').read_text())
for item in items:
    path=(root/item['path']).resolve()
    assert path.is_relative_to((root/'docs/reference').resolve())
    assert path.name.endswith('.py.txt')
    assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
    assert item['classification']=='historical-inert-reference'
    ast.parse(path.read_text())
print(json.dumps({'evidence_class':'reference_bytes_and_syntax','files':len(items),'status':'passed','execution':'not_run'}))
