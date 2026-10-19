import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class DockerfileBuildTests(unittest.TestCase):
    def test_custom_context_and_frontend_dockerfile_take_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'deploy').mkdir()
            dockerfile = root / 'deploy' / 'Wine.Dockerfile'
            dockerfile.write_text('FROM example/base\n')
            task = {'buildMode': 'dockerfile', 'appType': 'frontend',
                    'dockerfilePath': 'deploy/Wine.Dockerfile', 'dockerContext': 'deploy'}
            result = server.generate_dockerfile(task, root / 'nonexistent', root)
            self.assertEqual(result, (dockerfile.resolve(), (root / 'deploy').resolve(), None))

    def test_missing_files_and_repository_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for value in ('../outside', '/tmp/outside'):
                with self.assertRaises(ValueError):
                    server.repository_build_path(root, value, 'context')
            (root / 'escape').symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                server.repository_build_path(root, 'escape/file', 'Dockerfile')
            with self.assertRaisesRegex(ValueError, 'Dockerfile 不存在'):
                server.generate_dockerfile({'buildMode': 'dockerfile'}, root, root)

    def test_build_args_use_custom_pywine_address_without_shell_expansion(self):
        task = {'buildMode': 'dockerfile', 'language': 'python', 'sdk': 'pywine:3.13',
                'dockerPlatform': 'linux/amd64', 'dockerBuildArgs': 'VALUE=a b;$(echo test)',
                'sdkImages': [{'language': 'python', 'sdk': 'pywine:3.13',
                               'builderImage': 'private.example/wine:3.13'}]}
        command = server.docker_build_command(task, 'app:1', Path('/repo/Dockerfile'), Path('/repo'))
        self.assertIn('PYWINE_IMAGE=private.example/wine:3.13', command)
        self.assertIn('VALUE=a b;$(echo test)', command)
        self.assertEqual(command[-1], '/repo')
        self.assertIn('linux/amd64', command)
        task['dockerBuildArgs'] = 'PYWINE_IMAGE=override/wine:3.13'
        self.assertIn('PYWINE_IMAGE=override/wine:3.13', server.docker_build_command(task, 'app:1', Path('D'), Path('.')))

    def test_migration_preserves_custom_images_and_allows_later_removal(self):
        state = {'platformSettings': {'sdkImagesInitialized': True, 'sdkImages': [
            {'language': 'python', 'sdk': 'python3.12', 'builderImage': 'private/python'}]}}
        server.normalize_platform_settings(state)
        images = state['platformSettings']['sdkImages']
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0]['builderImage'], 'private/python')
        state['platformSettings']['sdkImages'] = images[:1]
        server.normalize_platform_settings(state)
        self.assertEqual(len(state['platformSettings']['sdkImages']), 1)

    def test_custom_save_does_not_require_compilation(self):
        payload = {'name': 'wine', 'repo': 'https://example/repo', 'buildMode': 'dockerfile'}
        # Stop before the state mutation; reaching this point proves validation passed.
        with patch.object(server, 'mutate_state', side_effect=RuntimeError('validated')):
            with self.assertRaisesRegex(RuntimeError, 'validated'):
                server.save_task_config('', payload, 'admin')
        with self.assertRaisesRegex(ValueError, '编译命令不能为空'):
            server.save_task_config('', {**payload, 'buildMode': 'auto'}, 'admin')

    def test_architecture_is_carried_to_deployment(self):
        task = {'name': 'wine', 'buildMode': 'dockerfile', 'dockerPlatform': 'linux/amd64'}
        manifest = server.create_manifest(task, {}, 'app:1')
        self.assertIn('kubernetes.io/arch: amd64', manifest)
        self.assertIn('kubernetes.io/os: linux', manifest)
        self.assertNotIn('nodeSelector:', server.create_manifest({'name': 'old'}, {}, 'app:1'))


if __name__ == '__main__':
    unittest.main()
