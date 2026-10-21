import importlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PackageContractTests(unittest.TestCase):
    def test_portable_manifest_resolves_hook_and_stdio_entry(self):
        manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        hook_path = manifest["extensions"]["com.openai"]["hooks"]
        self.assertTrue((ROOT / hook_path).is_file())
        servers = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(set(servers), {"experience-memory"})
        server = servers["experience-memory"]
        self.assertEqual(server["type"], "stdio")
        script = server["args"][0].replace("${PLUGIN_ROOT}/", "")
        self.assertTrue((ROOT / script).is_file())
        self.assertEqual(server["args"][1], "mcp")

    def test_package_import_has_no_sdk_side_effect(self):
        package = importlib.import_module("experience_memory")
        self.assertEqual(package.__version__, "0.1.0")
