import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2] / "src/experience_memory"


class LayerDependencyTests(unittest.TestCase):
    def test_layers_obey_dependency_direction(self):
        forbidden = {
            "domain": {"application", "infrastructure", "interfaces", "bootstrap", "config", "mcp", "pydantic", "urllib", "httpx2"},
            "application": {"infrastructure", "interfaces", "bootstrap", "config", "mcp", "pydantic", "urllib", "httpx2"},
            "infrastructure": {"application", "interfaces", "bootstrap", "config", "mcp"},
            "interfaces": {"domain", "infrastructure", "bootstrap", "config"},
        }
        for layer, blocked in forbidden.items():
            for path in (ROOT / layer).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        modules = [node.module or ""]
                    else:
                        continue
                    for module in modules:
                        with self.subTest(path=path.name, module=module):
                            self.assertFalse(set(module.split(".")) & blocked)

    def test_transport_does_not_reach_ports_or_storage_clients(self):
        # Prevent bootstrap Runtime ports from being exposed to transport entry functions.
        for path in (ROOT / "interfaces").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    with self.subTest(path=path.name, attribute=node.attr):
                        self.assertNotIn(node.attr, {"experiences", "controls", "sessions", "_experiences", "_controls", "_sessions"})
