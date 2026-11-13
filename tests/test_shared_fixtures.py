"""Standalone refinery consumer vectors; no runtime transport or serving claims."""
import copy
import hashlib
import importlib.util
import json
import marshal
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'fixtures'
BUNDLE = FIXTURES / 'shared-v1'
SOURCE_COMMIT = 'e8cd5779968fede213443db13a591c992cd0e183'
KINDS = {'feedback.observed', 'candidate.observed'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_pin(directory=FIXTURES):
    """Independent consumer integrity check before executing vendored validator."""
    pin = json.loads((directory / 'shared-v1.pin.json').read_text())
    bundle = directory / 'shared-v1'
    origin = directory / 'shared-v1.origin-manifest.json'
    if (pin['source_repository'], pin['source_commit'], pin['source_path'], pin['schema'], pin['component']) != (
            'newdarwindev/runspindle', SOURCE_COMMIT, 'fixtures/v1', 'runspindle.fixture.v1', 'refinery'):
        raise ValueError('source pin mismatch')
    origin_raw = origin.read_bytes()
    manifest_raw = (bundle / 'manifest.json').read_bytes()
    if hashlib.sha256(origin_raw).hexdigest() != pin['origin_manifest_sha256'] or hashlib.sha256(manifest_raw).hexdigest() != pin['subset_manifest_sha256']:
        raise ValueError('manifest pin mismatch')
    original = json.loads(origin_raw)
    manifest = json.loads(manifest_raw)
    if set(pin['subset_kinds']) != KINDS or set(original['components']['refinery']) != KINDS:
        raise ValueError('wrong consumer subset')
    if manifest['component'] != 'refinery' or manifest['schema'] != pin['schema'] or manifest['origin_manifest_sha256'] != pin['origin_manifest_sha256']:
        raise ValueError('origin binding mismatch')
    expected = {'artifact.txt', 'contract.json', 'fixturekit.py', 'golden.json', 'vectors.json'}
    if set(manifest['files']) != expected:
        raise ValueError('manifest inventory mismatch')
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if actual != expected | {'manifest.json'}:
        raise ValueError('bundle inventory mismatch')
    verified = {}
    for name, expected_hash in manifest['files'].items():
        path = bundle / name
        if path.is_symlink():
            raise ValueError('bundle digest mismatch')
        verified[name] = path.read_bytes()
        if hashlib.sha256(verified[name]).hexdigest() != expected_hash:
            raise ValueError('bundle digest mismatch')
    for unchanged in ['fixturekit.py', 'contract.json', 'artifact.txt']:
        if hashlib.sha256(verified[unchanged]).hexdigest() != original['files'][unchanged]:
            raise ValueError('source bytes mismatch')
    return pin, verified['fixturekit.py']


def load_kit(directory=FIXTURES):
    # Compile only the exact source snapshot whose digest passed the reviewed
    # pin. Import machinery must not substitute an unpinned bytecode cache.
    _, source = verify_pin(directory)
    path = directory / 'shared-v1/fixturekit.py'
    module = ModuleType('refinery_shared_fixturekit')
    module.__file__ = str(path)
    exec(compile(source, str(path), 'exec'), module.__dict__)
    return module


kit = load_kit()


class SharedFixtureTests(unittest.TestCase):
    def setUp(self):
        self.patches = []
        for method in ['socket', 'create_connection', 'getaddrinfo']:
            denied = patch.object(socket, method, side_effect=AssertionError('network forbidden'))
            spy = denied.start()
            self.patches.append(denied)
            self.addCleanup(denied.stop)
            self.addCleanup(spy.assert_not_called)
        self.golden = kit.load_json(BUNDLE / 'golden.json')
        self.by_kind = {f['kind']: f for f in self.golden}

    def reject(self, code, operation):
        with self.assertRaises(kit.ContractError) as error:
            operation()
        self.assertEqual(str(error.exception), code)

    def oracle(self, fixture):
        # Snapshot independently of any mutated adapter result. The candidate's
        # approved bytes are an explicit synthetic oracle, not an approval API.
        result = {fixture['state']: {'identity': copy.deepcopy(fixture['identity']), 'detail': copy.deepcopy(fixture['detail'])}}
        if fixture['kind'] == 'candidate.observed':
            result['approved_digest'] = hashlib.sha256(b'Synthetic candidate v1').hexdigest()
        return result

    def test_pinned_subset_inventory_and_source_integrity(self):
        pin, _ = verify_pin()
        self.assertEqual(pin['source_commit'], SOURCE_COMMIT)
        kit.verify_bundle(BUNDLE)
        self.assertEqual({f['kind'] for f in self.golden}, KINDS)
        self.assertEqual(len(self.golden), len(KINDS))
        self.assertEqual({v['kind'] for v in kit.load_json(BUNDLE / 'vectors.json')}, KINDS)
        self.assertEqual(len(kit.load_json(BUNDLE / 'vectors.json')), 4)

    def test_all_relevant_positive_vectors(self):
        for original in self.golden:
            with self.subTest(kind=original['kind']):
                identity = copy.deepcopy(original['identity'])
                self.assertTrue(kit.assert_conforms(copy.deepcopy(original), identity, original['state'], self.oracle(original)))
                self.assertEqual(original['identity'], identity)
        self.assertEqual(self.by_kind['candidate.observed']['identity']['candidate_digest'], hashlib.sha256(b'Synthetic candidate v1').hexdigest())

    def test_all_published_negative_vectors_use_unmutated_oracles(self):
        for vector in kit.load_json(BUNDLE / 'vectors.json'):
            original = self.by_kind[vector['kind']]
            expected_identity, observations = copy.deepcopy(original['identity']), self.oracle(original)
            broken = copy.deepcopy(original)
            cursor = broken
            for key in vector['mutation']['path'][:-1]:
                cursor = cursor[key]
            cursor[vector['mutation']['path'][-1]] = vector['mutation']['value']
            with self.subTest(vector=vector['id']):
                self.reject(vector['expected_error'], lambda: kit.assert_conforms(broken, expected_identity, original['state'], observations))
            self.assertEqual(expected_identity, original['identity'])

    def test_every_refinery_identity_field_is_bound_to_independent_request(self):
        for original in self.golden:
            for name, value in original['identity'].items():
                broken = copy.deepcopy(original)
                broken['identity'][name] = value + 1 if type(value) is int else ('0' * 64 if name.endswith('_digest') else 'synthetic-other')
                with self.subTest(kind=original['kind'], field=name):
                    self.reject('identity-mismatch', lambda: kit.assert_conforms(broken, copy.deepcopy(original['identity']), original['state'], self.oracle(original)))

    def test_serving_needs_observed_receipt_and_exact_approved_bytes(self):
        original = self.by_kind['candidate.observed']
        self.reject('missing-independent-receipt', lambda: kit.assert_conforms(original, original['identity'], 'serving'))
        broken = copy.deepcopy(original)
        broken['detail']['receipt_id'] = 'synthetic-forged-receipt'
        self.reject('missing-independent-receipt', lambda: kit.assert_conforms(broken, copy.deepcopy(original['identity']), 'serving', self.oracle(original)))
        observations = self.oracle(original)
        observations['approved_digest'] = hashlib.sha256(b'Changed synthetic candidate bytes').hexdigest()
        self.reject('unapproved-candidate', lambda: kit.assert_conforms(original, original['identity'], 'serving', observations))
        observations.pop('approved_digest')
        self.reject('unapproved-candidate', lambda: kit.assert_conforms(original, original['identity'], 'serving', observations))

    def test_refinery_unknown_outcomes_never_become_success(self):
        for original in self.golden:
            broken = copy.deepcopy(original)
            broken['state'] = 'unknown'
            self.reject('uncertainty-is-not-success', lambda: kit.validate(broken))
            for status in ['not-run', 'failed', 'blocked', 'unknown']:
                broken['status'] = status
                kit.validate(broken)
                self.assertEqual(kit.aggregate(['passed', status]), 'blocked')
        self.assertEqual(kit.aggregate(['passed', 'passed']), 'passed')

    def test_missing_extra_and_malformed_fields_reject(self):
        for original in self.golden:
            for name in original['identity']:
                broken = copy.deepcopy(original)
                del broken['identity'][name]
                with self.subTest(missing=name):
                    self.reject('required-keys', lambda: kit.validate(broken))
            for name, value, code in [('intent_revision', True, 'field-type-or-bound'), ('attempt', 0, 'field-type-or-bound'), ('sequence', 2**53, 'field-type-or-bound'), ('source_text', 'synthetic-extra', 'unknown-key')]:
                broken = copy.deepcopy(original)
                broken['identity'][name] = value
                self.reject(code, lambda: kit.validate(broken))
            broken = copy.deepcopy(original)
            broken['observed_at'] = None
            self.reject('field-type-or-bound', lambda: kit.validate(broken))

    def test_pinned_loader_rejects_tampering_and_consistent_manifest_rewrite(self):
        with tempfile.TemporaryDirectory(prefix='refinery-consumer-pin-') as temporary:
            root = Path(temporary)
            shutil.copytree(FIXTURES, root / 'fixtures')
            copied = root / 'fixtures'
            path = copied / 'shared-v1/golden.json'
            path.write_bytes(path.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'bundle digest'):
                verify_pin(copied)
            manifest_path = copied / 'shared-v1/manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['files']['golden.json'] = sha(path)
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'manifest pin'):
                verify_pin(copied)

    def test_planted_bytecode_cache_never_replaces_verified_source(self):
        with tempfile.TemporaryDirectory(prefix='refinery-bytecode-') as temporary:
            directory = Path(temporary) / 'fixtures'
            shutil.copytree(FIXTURES, directory, ignore=shutil.ignore_patterns('__pycache__'))
            source = directory / 'shared-v1/fixturekit.py'
            with patch.object(sys, 'pycache_prefix', None):
                cache = Path(importlib.util.cache_from_source(str(source)))
                cache.parent.mkdir()
                # A valid timestamp/size header would be accepted by Python's
                # source-file import loader even though these bytes are unpinned.
                header = importlib.util.MAGIC_NUMBER + struct.pack('<III', 0, int(source.stat().st_mtime), source.stat().st_size)
                planted = compile("raise AssertionError('unpinned bytecode executed')", str(source), 'exec')
                cache.write_bytes(header + marshal.dumps(planted))
                loaded = load_kit(directory)
                self.assertEqual(loaded.VERSION, 'runspindle.fixture.v1')
                loaded.verify_bundle(directory / 'shared-v1')
                # Prove the planted cache is valid and catches the previous
                # source-file loader behavior, rather than passing by accident.
                old_spec = importlib.util.spec_from_file_location('deliberately_broken_loader', source)
                with self.assertRaisesRegex(AssertionError, 'unpinned bytecode executed'):
                    old_spec.loader.exec_module(importlib.util.module_from_spec(old_spec))

    def test_loader_compiles_verified_snapshot_without_rereading_source(self):
        with tempfile.TemporaryDirectory(prefix='refinery-source-snapshot-') as temporary:
            directory = Path(temporary) / 'fixtures'
            shutil.copytree(FIXTURES, directory, ignore=shutil.ignore_patterns('__pycache__'))
            source = directory / 'shared-v1/fixturekit.py'
            read = Path.read_bytes
            source_reads = []
            def observed_read(path):
                if path == source:
                    source_reads.append(path)
                    if len(source_reads) > 1:
                        return b"raise AssertionError('unverified source reread executed')"
                return read(path)
            with patch.object(Path, 'read_bytes', observed_read):
                loaded = load_kit(directory)
            self.assertEqual(loaded.VERSION, 'runspindle.fixture.v1')
            self.assertEqual(len(source_reads), 1)

    def test_standalone_subset_runs_without_siblings_and_without_network(self):
        # Fresh process patches all Python socket creation and DNS before running
        # the standalone validator. This is not an OS network sandbox claim.
        script = "import runpy,socket,sys; deny=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden')); socket.socket=socket.create_connection=socket.getaddrinfo=deny; path=sys.argv[1]; sys.argv=[path]; runpy.run_path(path,run_name='__main__')"
        with tempfile.TemporaryDirectory(prefix='refinery-fixture-standalone-') as temporary:
            destination = Path(temporary) / 'shared-v1'
            shutil.copytree(BUNDLE, destination, ignore=shutil.ignore_patterns('__pycache__'))
            run = subprocess.run([sys.executable, '-c', script, str(destination / 'fixturekit.py')], cwd=temporary, capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(run.stdout)
            self.assertEqual(report['evidence_class'], 'synthetic_fixture_integrity')
            self.assertEqual(report['integration'], 'not-run')
            self.assertEqual(report['live_acceptance'], 'not-run')


if __name__ == '__main__':
    unittest.main()
