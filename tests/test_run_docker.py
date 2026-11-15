"""Exercise launcher session identity and lifecycle without compiling ATS."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


MOCK_DOCKER = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time, uuid
args = sys.argv[1:]
root = pathlib.Path(os.environ['MOCK_DOCKER_DIR'])
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps(args) + '\n')
def save(data):
    path = root / (data['Id'] + '.json')
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data))
    temp.replace(path)
if args[0] == 'build':
    sys.exit(int(os.environ.get('MOCK_BUILD_EXIT', '0')))
if args[:2] == ['image', 'inspect']:
    print('sha256:' + 'a' * 64 if args[args.index('--format') + 1] == '{{.Id}}' else 'arm64'); sys.exit(0)
if args[:2] == ['context', 'show']:
    print('desktop-linux'); sys.exit(0)
if args[0] == 'create':
    labels = dict(args[i + 1].split('=', 1) for i, arg in enumerate(args) if arg == '--label')
    project = labels['ats-helper.project_dir']
    data = dict(Id=uuid.uuid4().hex * 2, Name='/mock-container', Image='sha256:mock-image',
                Config=dict(Image='ats-helper-ubuntu:24.04', WorkingDir='/ats-helper', Labels=labels),
                Created='2026-11-12T00:00:00Z',
                State=dict(Status='created', Running=False, ExitCode=0),
                Mounts=[dict(Type='bind', Source=project, Destination='/ats-helper', RW=True),
                        dict(Type='volume', Name='ats-helper-build-arm64', Destination='/ats-build', RW=True)])
    save(data); print(data['Id']); sys.exit(0)
container_id = args[-1]
path = root / (container_id + '.json')
if not path.exists():
    sys.exit(1)
data = json.loads(path.read_text())
if args[0] == 'inspect':
    template = args[args.index('--format') + 1]
    if template == '{{.State.Status}}':
        print(data['State']['Status'])
    elif template == '{{.State.ExitCode}}':
        print(data['State']['ExitCode'])
    else:
        labels = data['Config']['Labels']
        record = dict(schema_version=1, container_id=data['Id'], container_name=data['Name'],
                      docker_context=labels['ats-helper.context'], image=data['Config']['Image'],
                      image_id=data['Image'], architecture=labels['ats-helper.architecture'],
                      mode=labels['ats-helper.mode'], project_dir=labels['ats-helper.project_dir'],
                      working_directory=data['Config']['WorkingDir'], created_at=data['Created'],
                      state_at_recording=dict(status=data['State']['Status'], running=data['State']['Running'],
                                              exit_code=data['State']['ExitCode']), mounts=data['Mounts'])
        print(json.dumps(record))
elif args[0] == 'start':
    data['State'].update(Status='running', Running=True)
    save(data)
    if os.environ.get('MOCK_DETACH') != '1':
        time.sleep(float(os.environ.get('MOCK_DELAY', '0')))
        data['State'].update(Status='exited', Running=False,
                             ExitCode=int(os.environ.get('MOCK_WORKLOAD_EXIT', '0')))
        save(data)
    # Intentionally return success: the launcher must inspect the workload's exit code.
elif args[0] == 'rm':
    path.unlink()
else:
    raise SystemExit('Unexpected Docker command: ' + repr(args))
'''


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ats-launcher-')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.project = base / 'workspace "with spaces"'
        self.project.mkdir()
        self.docker_state = base / 'docker-state'
        self.docker_state.mkdir()
        self.bin_dir = base / 'bin'
        self.bin_dir.mkdir()
        source = Path(__file__).resolve().parents[1] / 'run_docker.sh'
        shutil.copy2(source, self.project / 'run_docker.sh')
        fixture = self.project / 'cases/example-case'
        fixture.mkdir(parents=True)
        (fixture / 'case.json').write_text('{"name": "example-case"}\n')
        docker = self.bin_dir / 'docker'
        docker.write_text(MOCK_DOCKER)
        docker.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin_dir) + os.pathsep + os.environ['PATH'],
                        MOCK_DOCKER_DIR=str(self.docker_state))

    def launch(self, *options, **overrides):
        return subprocess.run(['bash', str(self.project / 'run_docker.sh'), *options],
                              env=dict(self.env, **overrides), capture_output=True, text=True)

    def record(self):
        return json.loads((self.project / '.docker/container.json').read_text())

    def calls(self):
        return [json.loads(line) for line in (self.docker_state / 'calls.jsonl').read_text().splitlines()]

    def test_record_has_exact_identity_mounts_and_exited_state(self):
        result = self.launch('--build-ats-ecosim')
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.record()
        self.assertRegex(record['container_id'], r'^[0-9a-f]{64}$')
        self.assertEqual(record['project_dir'], str(self.project))
        self.assertEqual(record['mounts'][0]['Source'], str(self.project))
        self.assertEqual(record['docker_context'], 'desktop-linux')
        self.assertEqual(record['mode'], 'build')
        self.assertEqual(record['state_at_recording']['status'], 'exited')
        self.assertFalse((self.docker_state / (record['container_id'] + '.json')).exists())

    def test_workload_failure_is_not_hidden_by_successful_attach(self):
        result = self.launch('--build-ats-ecosim', MOCK_WORKLOAD_EXIT='9')
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertEqual(self.record()['state_at_recording']['exit_code'], 9)

    def test_detached_shell_is_retained_and_recorded_running(self):
        result = self.launch(MOCK_DETACH='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.record()
        self.assertTrue(record['state_at_recording']['running'])
        self.assertTrue((self.docker_state / (record['container_id'] + '.json')).exists())
        self.assertFalse(any(call[0] == 'rm' for call in self.calls()))
        start = next(call for call in self.calls() if call[0] == 'start')
        self.assertIn('--interactive', start)

    def test_image_build_failure_does_not_create_a_container(self):
        result = self.launch('--build-ats-ecosim', MOCK_BUILD_EXIT='13')
        self.assertEqual(result.returncode, 13)
        self.assertFalse(any(call[0] == 'create' for call in self.calls()))
        self.assertFalse((self.project / '.docker/container.json').exists())

    def test_reproduction_uses_its_own_volume_and_forwards_prepare_mode(self):
        result = self.launch('--reproduce', 'example-case', '--prepare-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record()['mode'], 'reproduce')
        create = next(call for call in self.calls() if call[0] == 'create')
        mount = next(arg for arg in create if arg.startswith('type=volume,'))
        self.assertRegex(mount, r'source=ats-helper-reproduce-example-case-[0-9a-f]{12}-arm64-a{12},')
        self.assertEqual(create[-2:], ['example-case', '--prepare-only'])
        self.assertIn('/ats-helper/docker/reproduce-case.sh', create)
        self.assertTrue(any(arg.startswith('ATS_REPRODUCE_IMAGE_ID=') for arg in create))

    def test_unknown_or_conflicting_case_fails_before_docker(self):
        for args in (('--reproduce', '../elsewhere'), ('--reproduce', 'unknown'),
                     ('--prepare-only',), ('--reproduce', 'example-case', '--build-ats-ecosim')):
            with self.subTest(args=args):
                result = self.launch(*args)
                self.assertEqual(result.returncode, 2)
        self.assertFalse((self.docker_state / 'calls.jsonl').exists())

    def test_older_session_finishing_preserves_newer_record(self):
        older = subprocess.Popen(['bash', str(self.project / 'run_docker.sh'), '--build-ats-ecosim'],
                                 env=dict(self.env, MOCK_DELAY='2'), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: older.kill() if older.poll() is None else None)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(json.loads(path.read_text())['State']['Running']
                   for path in self.docker_state.glob('*.json')):
                break
            if older.poll() is not None:
                self.fail(older.stderr.read())
            time.sleep(0.02)
        else:
            self.fail('Older session did not start')
        newer = self.launch('--build-ats-ecosim', MOCK_WORKLOAD_EXIT='7')
        self.assertEqual(newer.returncode, 7, newer.stderr)
        newer_id = self.record()['container_id']
        _, error = older.communicate(timeout=5)
        self.assertEqual(older.returncode, 0, error)
        self.assertEqual(self.record()['container_id'], newer_id)
        histories = list((self.project / '.docker/containers').glob('*.json'))
        self.assertEqual(len(histories), 2)
        self.assertTrue(all(json.loads(path.read_text())['state_at_recording']['status'] == 'exited'
                            for path in histories))


if __name__ == '__main__':
    unittest.main()
