import json
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / "windows-agent"


class WinSWTemplateTests(unittest.TestCase):
    def test_shipped_agent_configs_have_valid_json_and_windows_paths(self):
        for filename in ("config.json", "config.example.json"):
            with self.subTest(config=filename):
                config = json.loads((AGENT_DIR / filename).read_text(encoding="utf-8-sig"))
                paths = [config["stateDirectory"]]
                for application in config["applications"].values():
                    paths.extend(application[key] for key in ("InstallRoot", "Python", "Uv", "ServiceWrapper"))
                for path in paths:
                    self.assertRegex(path, r"^[A-Za-z]:/")
                    self.assertNotIn("\\", path)

    def test_all_service_templates_include_winsw_required_metadata(self):
        for filename in ("Install-Agent.ps1", "Initialize-Mt5Service.ps1", "Invoke-Mt5Release.ps1"):
            with self.subTest(script=filename):
                source = (AGENT_DIR / filename).read_text()
                template = re.search(r'\$xml = @"\r?\n(.*?)\r?\n"@', source, re.S).group(1)
                # Dynamic PowerShell values are not part of the static XML structure.
                template = re.sub(r"\$\(& \$escape [^\r\n]*?\)(?:\))?", "value", template)
                template = re.sub(r"\$(?:environmentXml|[A-Za-z]+)", "value", template)
                service = ET.fromstring(template)
                self.assertEqual(service.tag, "service")
                for name in ("id", "name", "description", "executable"):
                    value = service.find(name)
                    self.assertIsNotNone(value, name)
                    self.assertTrue(value.text and value.text.strip(), name)

    def test_default_config_path_is_resolved_after_parameter_binding(self):
        source = (AGENT_DIR / "Install-Agent.ps1").read_text()
        parameters, body = source.split("$ErrorActionPreference = 'Stop'", 1)
        self.assertNotIn("$PSScriptRoot", parameters)
        self.assertRegex(parameters, r"\[string\]\$ConfigPath\s*=\s*''")
        self.assertIn("$ConfigPath = Join-Path $PSScriptRoot 'config.json'", body)
        self.assertLess(body.index("Join-Path $PSScriptRoot"), body.index("Resolve-Path -LiteralPath $ConfigPath"))


if __name__ == "__main__":
    unittest.main()
