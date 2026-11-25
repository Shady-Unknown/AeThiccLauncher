"""The Discovery split keeps one class object per widget (G1)."""
import ast
import unittest
from pathlib import Path

from ui.tabs import discovery_rows, discovery_scroll, discovery_tab

ROOT = Path(__file__).resolve().parents[1]


def _imports(module: str) -> set[str]:
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


class ReExportTests(unittest.TestCase):
    def test_old_path_exports_the_same_class_objects(self):
        self.assertIs(discovery_tab.PhysicsScrollArea,
                      discovery_scroll.PhysicsScrollArea)
        self.assertIs(discovery_tab.CategoryRow, discovery_rows.CategoryRow)
        self.assertIs(discovery_tab.InfiniteCategoryScroller,
                      discovery_rows.InfiniteCategoryScroller)

    def test_no_compatibility_subclasses(self):
        for cls in (discovery_tab.PhysicsScrollArea,
                    discovery_tab.CategoryRow,
                    discovery_tab.InfiniteCategoryScroller):
            self.assertEqual(cls.__subclasses__(), [])


class DependencyTests(unittest.TestCase):
    def test_scroll_stays_at_the_bottom(self):
        imports = _imports("ui/tabs/discovery_scroll.py")
        self.assertNotIn("ui.tabs.discovery_rows", imports)
        self.assertNotIn("ui.tabs.discovery_tab", imports)

    def test_rows_do_not_import_the_tab(self):
        imports = _imports("ui/tabs/discovery_rows.py")
        self.assertNotIn("ui.tabs.discovery_tab", imports)

    def test_no_cycles_between_the_three_modules(self):
        graph = {
            "ui.tabs.discovery_scroll": _imports("ui/tabs/discovery_scroll.py"),
            "ui.tabs.discovery_rows": _imports("ui/tabs/discovery_rows.py"),
            "ui.tabs.discovery_tab": _imports("ui/tabs/discovery_tab.py"),
        }
        for name, imports in graph.items():
            for other in graph:
                if other != name:
                    self.assertFalse(
                        other in imports and name in graph[other],
                        f"cycle between {name} and {other}",
                    )


if __name__ == "__main__":
    unittest.main()
