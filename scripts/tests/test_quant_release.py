"""Source mismatches and paused research workers cannot become release success."""

import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import quant_release as release
import release_install


class ResearchSource(unittest.TestCase):
    def test_image_content_must_match_the_entire_repository_inventory(self):
        for fault in ('changed', 'missing', 'extra', 'symlink'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                staging, target = root / 'staging', root / 'target'
                staging.mkdir()
                (staging / 'source.py').write_text('checked')
                expected = {'source.py': release.blob(b'checked')}
                if fault == 'changed':
                    (staging / 'source.py').write_text('different')
                elif fault == 'missing':
                    expected['missing.py'] = release.blob(b'missing')
                elif fault == 'extra':
                    (staging / 'unexpected.py').write_text('extra')
                else:
                    (staging / 'link').symlink_to(staging / 'source.py')
                with self.assertRaises(ValueError):
                    release.install_source(staging, target, expected)
                self.assertFalse(target.exists())

    def test_source_installation_reconciles_identical_files_and_refuses_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging, target = root / 'staging', root / 'target'
            staging.mkdir()
            (staging / 'source.py').write_text('checked')
            expected = {'source.py': release.blob(b'checked')}
            first = release.install_source(staging, target, expected)
            self.assertEqual(release.install_source(staging, target, expected), first)
            (target / 'source.py').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'immutable research source changed'):
                release.install_source(staging, target, expected)
            self.assertEqual((target / 'source.py').read_text(), 'changed')

    def test_untracked_python_source_cannot_hide_beside_the_checked_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'checked.py').write_text('checked')
            (root / 'sitecustomize.py').write_text('unexpected')
            with self.assertRaisesRegex(ValueError, 'inventory changed'):
                release.verify_inventory(root, {'checked.py': hashlib.sha256(b'checked').hexdigest()})

    def test_dependency_change_fails_before_any_container_or_worker_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'control').mkdir()
            (root / 'source' / ('a' * 40) / 'backend').mkdir(parents=True)
            (root / 'control/config.json').write_text(json.dumps({'source_revision': 'a' * 40}))
            (root / 'control/quant_host.py').write_text('fixed controller')
            candidate = root / 'candidate'
            candidate.mkdir()
            (candidate / 'release-manifest.json').write_text(json.dumps({'revision': 'b' * 40}))
            tree = [{'path': p, 'type': 'blob', 'mode': '100644', 'sha': release.blob(b'new dependency')} for p in ['backend/tauric/quant_worker.py', 'backend/runners/qlib_runner.py', *release.PATHS[2:]]]
            tree.append({'path': 'scripts/quant_host.py', 'type': 'blob', 'sha': release.blob(b'fixed controller')})
            for name in release.PATHS[2:]:
                (root / 'source' / ('a' * 40) / name).write_text('old dependency')
            with patch.object(release, 'ROOT', root), patch.object(release, 'run') as effects:
                with self.assertRaisesRegex(ValueError, 'runtime dependencies'):
                    release.prepare(candidate, tree, 'a' * 40)
                effects.assert_not_called()

    def prepared(self, root, units):
        (root / 'control').mkdir()
        (root / 'control/quant_host.py').write_text('fixed controller')
        original = {'source_revision': 'a' * 40}
        name = 'backend/controller.py'
        source = root / 'source' / ('b' * 40) / name
        source.parent.mkdir(parents=True)
        source.write_text('checked source')
        target = {'source_revision': 'b' * 40, 'source_files': {name: hashlib.sha256(source.read_bytes()).hexdigest()}}
        (root / 'control/config.json').write_text(json.dumps(original))
        return {'original': original, 'target': target, 'units': units, 'files_verified': 1, 'activation_started_epoch': time.time() - 1, 'controller_sha256': hashlib.sha256(b'fixed controller').hexdigest()}

    def test_explicitly_paused_workers_stay_paused_after_source_activation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = self.prepared(root, [])
            with patch.object(release, 'ROOT', root), patch.object(release, 'run') as effects:
                value = release.activate(prepared, Mock(), ['compose'])
            self.assertEqual(value['workers_resumed'], [])
            effects.assert_not_called()
            self.assertEqual(json.loads((root / 'control/config.json').read_text()), prepared['target'])

    def test_verified_resume_is_idempotent_and_requires_fresh_heartbeats(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            unit = release.UNITS[0]
            prepared = self.prepared(root, [unit])
            calls = []

            def run(args):
                calls.append(args)
                return b'inactive' if 'ActiveState' in ' '.join(args) else b''

            server = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(stdout=json.dumps({'lab_lean': time.time() - 0.01}).encode()))
            with patch.object(release, 'ROOT', root), patch.object(release, 'run', side_effect=run), patch.object(release, 'process_source', return_value=str(root / 'source' / ('b' * 40) / 'backend')):
                first = release.activate(prepared, server, ['compose'])
                again = release.activate(prepared, server, ['compose'])
            self.assertEqual(first['source_revision'], again['source_revision'])
            self.assertFalse(any('stop' in c or 'restart' in c for c in calls))
            self.assertFalse(any(release.UNITS[1] in c for c in calls))

    def test_old_running_controller_blocks_configuration_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = self.prepared(root, [release.UNITS[0]])
            with patch.object(release, 'ROOT', root), patch.object(release, 'run', return_value=b'active'), self.assertRaisesRegex(ValueError, 'still running'):
                release.activate(prepared, Mock(), ['compose'])
            self.assertEqual(json.loads((root / 'control/config.json').read_text()), prepared['original'])

    def test_pointer_reentry_preserves_the_original_checked_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            previous, candidate, other = (root / n for n in ('previous', 'candidate', 'other'))
            for p in (previous, candidate, other):
                p.mkdir()
            pointer = root / 'current'
            pointer.symlink_to(previous)
            release_install.publish_current(previous, candidate, pointer)
            release_install.publish_current(previous, candidate, pointer)
            self.assertEqual(pointer.resolve(), candidate.resolve())
            pointer.unlink()
            pointer.symlink_to(other)
            with self.assertRaisesRegex(ValueError, 'pointer changed'):
                release_install.publish_current(previous, candidate, pointer)
            self.assertEqual(pointer.resolve(), other.resolve())

    def test_research_that_arrives_during_freeze_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            previous, candidate = root / 'previous', root / 'candidate'
            previous.mkdir()
            candidate.mkdir()
            images = {'IMAGE': 'digest'}
            (previous / 'release-manifest.json').write_text(json.dumps({'images': images}))
            (candidate / 'release-manifest.json').write_text(json.dumps({'revision': 'b' * 40}))
            commands = []
            server = SimpleNamespace(command=lambda *_: ['compose'], read_env=lambda *_: images, validate_images=lambda *_: None, run=lambda args, **kw: commands.append(args), IMAGE_KEYS=['IMAGE'])
            progress = {'phase': 'freeze_requested', 'previous_services': ['db', 'api']}
            with patch.object(release_install, 'module', return_value=server), patch.object(release_install, 'engineering_idle', return_value=True), patch.object(release, 'idle', return_value=False), patch.object(release, 'stop') as stop, self.assertRaises(release_install.EngineeringBusy):
                release_install.install(active_release=previous, candidate=candidate, state=root, project='fixture', revision='b' * 40, journal={'installation': progress}, journal_path=root / 'journal.json', authorize=lambda: None, tree=[])
            stop.assert_not_called()
            self.assertEqual(commands[-1][-2:], ['db', 'api'])
            self.assertEqual(progress, {})

    def test_old_heartbeat_cannot_establish_a_resumed_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = self.prepared(root, [release.UNITS[0]])
            (root / 'control/config.json').write_text(json.dumps(prepared['target']))
            server = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(stdout=json.dumps({'lab_lean': prepared['activation_started_epoch'] - 1}).encode()))
            with patch.object(release, 'ROOT', root), patch.object(release, 'run'), patch.object(release, 'process_source', return_value=str(root / 'source' / ('b' * 40) / 'backend')), patch.object(release.time, 'monotonic', side_effect=[0, 91]), self.assertRaisesRegex(ValueError, 'fresh heartbeat'):
                release.activate(prepared, server, ['compose'])

    def test_a_new_disabled_state_never_starts_the_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = self.prepared(root, [release.UNITS[0]])
            (root / 'control/config.json').write_text(json.dumps(prepared['target']))
            with patch.object(release, 'ROOT', root), patch.object(release, 'run', side_effect=RuntimeError('Disabled by owner')) as effect, self.assertRaisesRegex(RuntimeError, 'Disabled by owner'):
                release.activate(prepared, Mock(), ['compose'])
            self.assertEqual(effect.call_args.args[0], ['systemctl', 'is-enabled', '--quiet', release.UNITS[0]])

    def test_engineering_recovery_cannot_automatically_start_paused_research(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            automatic = root / 'tauric-engineering.service.wants'
            boot = root / 'multi-user.target.wants'
            automatic.mkdir()
            boot.mkdir()
            for unit in release.UNITS:
                (root / unit).write_text('recorded unit')
                (automatic / unit).symlink_to(root / unit)
                (boot / unit).symlink_to(root / unit)
            with patch.object(release, 'SYSTEMD', root), patch.object(release, 'run') as effect:
                release.detach_automatic_start()
                release.detach_automatic_start()
            self.assertFalse(list(automatic.iterdir()))
            self.assertEqual(len(list(boot.iterdir())), 2)
            effect.assert_called_once_with(['systemctl', 'daemon-reload'])

    def test_backup_resume_waits_for_api_health_and_only_starts_original_worker(self):
        calls = []
        states = iter(['starting', 'healthy'])

        def run(args):
            calls.append(args)
            if args[:2] == ['docker', 'inspect']:
                return json.dumps([{'State': {'Health': {'Status': next(states)}}}]).encode()
            return b''

        with patch.object(release, 'run', side_effect=run), patch.object(release.time, 'sleep'):
            release.resume_units([release.UNITS[0]])
        self.assertEqual(calls[:2], [['docker', 'inspect', 'tauric_server-api-1']] * 2)
        self.assertEqual(calls[-1], ['systemctl', 'start', release.UNITS[0]])
        self.assertFalse(any(release.UNITS[1] in c for c in calls))

    def test_unhealthy_api_exhausts_bounded_resume_without_starting_workers(self):
        unhealthy = json.dumps([{'State': {'Health': {'Status': 'unhealthy'}}}]).encode()
        with patch.object(release, 'run', return_value=unhealthy) as effect, patch.object(release.time, 'monotonic', side_effect=[0, 121]), self.assertRaisesRegex(ValueError, 'healthy API'):
            release.resume_units([release.UNITS[0]])
        effect.assert_called_once_with(['docker', 'inspect', 'tauric_server-api-1'])
