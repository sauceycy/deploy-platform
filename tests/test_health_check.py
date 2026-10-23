import unittest

import server


class HealthCheckTests(unittest.TestCase):
    def test_custom_port_only_changes_probe(self):
        manifest = server.create_manifest({
            'name': 'demo', 'containerPort': 8080,
            'healthCheck': {'enabled': True, 'path': '/health', 'port': '9090'},
        }, {}, 'demo:1')
        self.assertIn('containerPort: 8080', manifest)
        self.assertIn('path: "/health"\n              port: 9090', manifest)

    def test_optional_port_defaults_to_container_port(self):
        for config in ({}, {'port': ''}, {'port': None}):
            with self.subTest(config=config):
                manifest = server.create_manifest({
                    'name': 'demo', 'containerPort': 8080,
                    'healthCheck': {'enabled': True, 'path': '/health', **config},
                }, {}, 'demo:1')
                self.assertIn('path: "/health"\n              port: 8080', manifest)

    def test_port_validation(self):
        for port in (0, 65536, -1, 'abc', '1.5', True):
            with self.subTest(port=port), self.assertRaises(ValueError):
                server.normalize_health_check({'port': port})
        for port in (1, 65535):
            self.assertEqual(server.normalize_health_check({'port': str(port)})['port'], port)

    def test_disabled_probe_is_omitted(self):
        manifest = server.create_manifest({
            'name': 'demo', 'healthCheck': {'enabled': False, 'path': '/health', 'port': 9090},
        }, {}, 'demo:1')
        self.assertNotIn('readinessProbe:', manifest)
