import copy
import unittest
from unittest.mock import patch

import server as s


class NotificationConfigurationTests(unittest.TestCase):
    def test_agent_result_notification_uses_the_execution_configuration(self):
        deploy_config = {
            "id": "cfg-prod",
            "name": "trade-prod",
            "env": "prod",
            "deploymentName": "trade-market-prod",
            "clusters": [{"name": "trade-prod-eks", "namespace": "prod"}],
        }
        state = s.default_state_copy()
        state["tasks"] = [{
            "id": "task-1",
            "name": "trade-market",
            "env": "test",
            "deployRule": "k8s",
            "clusters": [{"name": "trade-test-eks"}],
            "deployConfigs": [deploy_config],
            "notify": {"events": ["发布成功"]},
        }]
        state["executions"] = [{
            "id": "execution-1",
            "taskId": "task-1",
            "status": "deploying",
            "deployConfigId": "cfg-prod",
            "deployConfigSnapshot": copy.deepcopy(deploy_config),
            "clusterResults": {"trade-prod-eks": "pending"},
        }]
        state["agentTasks"] = [{
            "id": "agent-task-1",
            "executionId": "execution-1",
            "taskId": "task-1",
            "clusterName": "trade-prod-eks",
            "status": "pending",
        }]

        def fake_mutate(callback, **_kwargs):
            callback_state = copy.deepcopy(state)
            result = callback(callback_state)
            return result, callback_state

        sent = []
        with patch.object(s, "mutate_state", side_effect=fake_mutate), \
                patch.object(s, "send_notification", side_effect=lambda *args: sent.append(args)), \
                patch.object(s, "append_publish_commit_log"):
            s.update_agent_result("agent-task-1", "success", "部署完成")

        self.assertEqual(len(sent), 1)
        notification_task = sent[0][0]
        self.assertEqual(notification_task["env"], "prod")
        self.assertEqual(notification_task["clusters"][0]["name"], "trade-prod-eks")
        card = s.lark_notification_card(notification_task, sent[0][1], "发布成功", sent[0][2], "2026-11-05 19:42:40", deploy_config)
        fields = card["card"]["elements"][0]["fields"]
        environment = next(field for field in fields if field["text"]["content"].startswith("**环境**"))
        self.assertIn("prod", environment["text"]["content"])


if __name__ == "__main__":
    unittest.main()
