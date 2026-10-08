import copy
import importlib.util
import json
import unittest
from pathlib import Path

import server as s
import windows_deploy as windows


spec = importlib.util.spec_from_file_location('env_test_agent', Path(__file__).resolve().parents[1] / 'windows-agent/windows_agent.py')
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class WindowsEnvTests(unittest.TestCase):
    def task(self):
        task = s.normalize_task_payload({'name': 'mt5', 'repo': 'https://example.com/repo.git', 'deployRule': 'windows',
                                         'deployConfigs': [
                                             {'id': 'test', 'name': 'test', 'windowsEnv': 'APP_ENV=test\nNACOS_PASSWORD=test-password', 'clusters': [{'name': 'win-test'}]},
                                             {'id': 'prod', 'name': 'prod', 'windowsEnv': 'APP_ENV=prod\nNACOS_PASSWORD=prod-password', 'clusters': [{'name': 'win-test'}]},
                                         ]})
        return {**task, 'id': 1}

    def state(self, task, capabilities=None):
        state = copy.deepcopy(s.DEFAULT_STATE)
        state['tasks'] = [task]
        state['clusters'] = [{'name': 'win-test', 'organizationId': 'default'}]
        state['agentHeartbeats'] = [{'cluster': 'win-test', 'instanceId': 'win-01', 'kind': 'windows',
                                     'time': s.now_text(), 'capabilities': capabilities or []}]
        return state

    def test_dotenv_preserves_paths_equals_hashes_and_supports_quotes_comments(self):
        content = '\ufeff# comment\r\nAPP_ENV=test\r\nexport NACOS_PASSWORD="a=b#c" # comment\nPATH_HINT=C:\\Tools\\bin\nNAME=中文 # note\nEMPTY=\nQUOTED=\'two words\'\nESCAPED="a\\nb\\\"c"'
        values = windows.parse_windows_env(content)
        self.assertEqual(values, {'APP_ENV': 'test', 'NACOS_PASSWORD': 'a=b#c', 'PATH_HINT': 'C:\\Tools\\bin',
                                  'NAME': '中文', 'EMPTY': '', 'QUOTED': 'two words', 'ESCAPED': 'a\nb"c'})

    def test_invalid_dotenv_reports_line_without_disclosing_values(self):
        for line in ('PRIVATE-secret', 'BAD KEY=PRIVATE-secret', 'KEY="PRIVATE-secret',
                     "KEY='PRIVATE-secret", 'KEY=${PRIVATE-secret}', 'KEY=one\nkey=PRIVATE-secret'):
            with self.subTest(line=line), self.assertRaises(ValueError) as failure:
                windows.parse_windows_env(line)
            self.assertIn('行', str(failure.exception))
            self.assertNotIn('PRIVATE-secret', str(failure.exception))
        for content in ('KEY=' + 'x' * 65537, 'KEY=\x00'):
            with self.assertRaises(ValueError):
                windows.parse_windows_env(content)

    def test_task_saving_rejects_invalid_environment_for_windows_only(self):
        invalid = {'name': 'mt5', 'deployRule': 'windows', 'deployConfigs': [{'windowsEnv': 'SECRET="private'}]}
        with self.assertRaises(ValueError):
            s.normalize_task_payload(invalid)
        self.assertEqual(s.normalize_task_payload({**invalid, 'deployRule': 'k8s'})['deployRule'], 'k8s')

    def test_selected_configuration_is_snapshotted_and_not_changed_by_later_edits(self):
        task = self.task()
        state = self.state(task, ['dotenv-v1'])
        execution = s.create_execution_record(state, task, 'admin', 'main', deploy_config=task['deployConfigs'][1])
        task['deployConfigs'][1]['windowsEnv'] = 'APP_ENV=prod\nNACOS_PASSWORD=changed-later'
        effective = s.effective_task_for_deploy_config(task, execution['deployConfigSnapshot'])
        windows.dispatch(s, state, execution, effective, 'deploy', {'sha256': 'a' * 64, 'size': 1})
        payload = state['agentTasks'][0]['payload']
        self.assertEqual(payload['envFile']['variables']['NACOS_PASSWORD'], 'prod-password')
        self.assertEqual(payload['envFile']['content'], 'APP_ENV=prod\nNACOS_PASSWORD=prod-password')
        self.assertNotIn('prod-password', json.dumps(execution['logs']))

    def test_each_configuration_dispatches_its_own_environment(self):
        for index, environment in ((0, 'test'), (1, 'prod')):
            task = self.task()
            state = self.state(task, ['dotenv-v1'])
            config = task['deployConfigs'][index]
            execution = s.create_execution_record(state, task, 'admin', 'main', deploy_config=config)
            windows.dispatch(s, state, execution, s.effective_task_for_deploy_config(task, config), 'deploy')
            self.assertEqual(state['agentTasks'][0]['payload']['envFile']['variables']['APP_ENV'], environment)

    def test_old_agent_is_rejected_without_creating_partial_tasks(self):
        task = self.task()
        state = self.state(task)
        execution = s.create_execution_record(state, task, 'admin', 'main')
        with self.assertRaisesRegex(ValueError, '更新并重启 Agent'):
            windows.dispatch(s, state, execution, s.effective_task_for_deploy_config(task, task['deployConfigs'][0]), 'deploy')
        self.assertEqual(state['agentTasks'], [])

    def test_blank_environment_preserves_server_file_and_legacy_agent_compatibility(self):
        task = self.task()
        task['deployConfigs'][0]['windowsEnv'] = ' \r\n'
        state = self.state(task)
        execution = s.create_execution_record(state, task, 'admin', 'main')
        windows.dispatch(s, state, execution, s.effective_task_for_deploy_config(task, task['deployConfigs'][0]), 'deploy')
        self.assertNotIn('envFile', state['agentTasks'][0]['payload'])

    def test_manual_rollback_uses_selected_environment(self):
        task = self.task()
        state = self.state(task, ['dotenv-v1'])
        config = task['deployConfigs'][1]
        execution = s.create_execution_record(state, task, 'admin', 'rollback', deploy_config=config)
        windows.dispatch(s, state, execution, s.effective_task_for_deploy_config(task, config), 'rollback')
        payload = state['agentTasks'][0]['payload']
        self.assertEqual(payload['envFile']['variables']['APP_ENV'], 'prod')
        self.assertNotIn('downloadPath', payload)

    def test_execution_summaries_and_agent_task_summaries_omit_environment_content(self):
        task = self.task()
        state = self.state(task, ['dotenv-v1'])
        execution = s.create_execution_record(state, task, 'admin', 'main')
        windows.dispatch(s, state, execution, s.effective_task_for_deploy_config(task, task['deployConfigs'][0]), 'deploy')
        for compact in (False, True):
            self.assertNotIn('test-password', json.dumps(s.execution_summary(execution, compact)))
            self.assertNotIn('test-password', json.dumps(s.agent_task_summary(state['agentTasks'][0], compact)))
        self.assertIn('test-password', execution['deployConfigSnapshot']['windowsEnv'])

    def instance(self):
        instance = agent.Agent.__new__(agent.Agent)
        instance.config = {'applications': {'mt5': {'InstallRoot': 'C:/MT5', 'Environment': {
            'APP_ENV': 'old', 'nacos_password': 'old-secret', 'KEEP': 'unchanged', 'python_mt5_sidecar_dotenv_enabled': 'true'}}}}
        instance.headers = {'X-Agent-Token': 'agent-token'}
        instance.current = None
        return instance

    def test_agent_env_overrides_local_defaults_without_mutating_agent_configuration(self):
        instance = self.instance()
        original = copy.deepcopy(instance.config)
        content = 'APP_ENV=prod\nNACOS_PASSWORD=new-secret'
        settings = instance.deployment_settings({'application': 'mt5', 'envFile': {'content': content, 'variables': windows.parse_windows_env(content)}})
        self.assertEqual(settings['EnvContent'], content)
        self.assertEqual(settings['Environment'], {'APP_ENV': 'prod', 'NACOS_PASSWORD': 'new-secret', 'PYTHON_MT5_SIDECAR_DOTENV_ENABLED': 'false'})
        self.assertEqual(instance.config, original)
        self.assertNotIn('EnvContent', instance.deployment_settings({'application': 'mt5'}))
        empty = instance.deployment_settings({'application': 'mt5', 'envFile': {'content': '', 'variables': {}}})
        self.assertNotIn('EnvContent', empty)
        self.assertEqual(empty['Environment'], original['applications']['mt5']['Environment'])

    def test_agent_masks_dotenv_content_and_values_from_deployment_logs(self):
        instance = self.instance()
        content = 'APP_ENV=prod\nNACOS_PASSWORD=unique-secret\nSHORT=x'
        instance.current = {'payload': {'envFile': {'content': content, 'variables': windows.parse_windows_env(content)}}}
        text = instance.redact('unique-secret\n' + content + '\nx')
        self.assertNotIn('unique-secret', text)
        self.assertNotIn('prod', text)
        self.assertNotIn('x', text)

    def test_agent_rejects_invalid_environment_payload(self):
        for env_file in (None, {'content': 'text', 'variables': {'BAD KEY': 'secret'}},
                         {'content': 'text', 'variables': {'KEY': '\x00'}},
                         {'content': 'text', 'variables': {'KEY': 1}}):
            with self.subTest(payload=env_file), self.assertRaises(ValueError):
                self.instance().deployment_settings({'application': 'mt5', 'envFile': env_file})


if __name__ == '__main__':
    unittest.main()
