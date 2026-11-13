#!/usr/bin/env python3
"""Dependency-free structural checks. Passing is not product acceptance."""
import json, pathlib, re, subprocess, sys
root = pathlib.Path(__file__).resolve().parents[1]
required = ['README.md','AGENTS.md','component.json','specs/README.md','provenance.json','STATUS.json']
for name in required:
    assert (root/name).is_file(), f'Missing {name}'
c = json.loads((root/'component.json').read_text())
s = json.loads((root/'STATUS.json').read_text())
assert c['stage'] == s['stage']
assert (c['stage'], s['implementation']) in {
    ('scaffold', 'not_implemented'),
    ('lineage_candidate', 'partial_lineage_model'),
}
if c['stage'] == 'lineage_candidate':
    for name in ['refinery/lineage.py', 'tests/test_lineage.py',
                 'docs/IMPLEMENTATION_EVIDENCE.md', 'docs/local-lineage-tests.json']:
        assert (root/name).is_file(), f'Missing {name}'
    assert s['lineage_tests'] in ['not_run', 'passed', 'failed']
    for name in ['durable_lineage', 'provider_adapter', 'candidate_serving', 'trusted_feedback_adapter']:
        assert s[name] == 'not_implemented'
    assert s['shared_contracts'] == 'provisional_not_integrated'
assert s['live_acceptance'] == 'not_run'
p = json.loads((root/'provenance.json').read_text())
for item in p['sources']:
    assert re.fullmatch(r'SRC-[0-9]{3}', item['source_id'])
    assert item['status'] in ['adapted_spec','adapted_document','pending_review','reference_only']
for path in (root/'.github/workflows').glob('*.yml'):
    text = path.read_text()
    lanes = re.findall(r'^\s*runs-on:\s*(.+)$', text, re.MULTILINE)
    assert lanes and all(lane == '[self-hosted, linux, x64, runspindle-isolated, strix]' for lane in lanes), path
    assert 'pull_request_target:' not in text and 'pull_request:' not in text, path
    assert 'persist-credentials: false' in text, path
    assert 'contents: read' in text, path
    for action in re.findall(r'uses:\s*([^\s]+)', text):
        assert re.fullmatch(r'[A-Za-z0-9_./-]+@[0-9a-f]{40}', action), (path, action)
    assert 'ubuntu-latest' not in text and 'macos-latest' not in text and 'windows-latest' not in text, path
subprocess.run([sys.executable,str(root/'tools/check_references.py')],check=True)
subprocess.run([sys.executable,str(root/'tools/check_artifacts.py')],check=True)
print(json.dumps({'status':'passed','evidence_class':'scaffold_structure','repository':c['repository'],'product_acceptance':'not_run'},sort_keys=True))
