import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: layer -> module prefixes it must never import.
FORBIDDEN = {
    "core": ("ui", "managers", "network"),
    "managers": ("ui",),
    "network": ("managers",),
}


def _imports(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield (node.module or ""), node.lineno


class LayerDirectionTests(unittest.TestCase):
    def test_forbidden_layer_imports(self):
        offenders = []
        for layer, forbidden in FORBIDDEN.items():
            for path in (ROOT / layer).rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                for module, lineno in _imports(path):
                    if any(module == bad or module.startswith(bad + ".")
                           for bad in forbidden):
                        offenders.append(
                            f"{path.relative_to(ROOT)}:{lineno} imports {module}")

        self.assertEqual(
            offenders, [],
            "Layer direction is one-way (ui -> managers/network -> core):\n"
            + "\n".join(offenders),
        )

    def test_managers_do_not_touch_the_window(self):
        offenders = []
        for path in (ROOT / "managers").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(ROOT)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "mw":
                    offenders.append(f"{rel}:{node.lineno} uses .mw")
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = list(node.args.args) + list(node.args.kwonlyargs)
                    for arg in args:
                        if arg.arg in ("mw", "app_window"):
                            offenders.append(
                                f"{rel}:{node.lineno} parameter {arg.arg}")
                if isinstance(node, ast.ImportFrom) and node.module and (
                        node.module == "PyQt6.QtWidgets"
                        or node.module.startswith("PyQt6.QtWebEngine")):
                    offenders.append(
                        f"{rel}:{node.lineno} imports {node.module}")

        self.assertEqual(
            offenders, [],
            "Managers must be headless: UI widgets live in ui/ and the window "
            "is injected as explicit services/callbacks.\n" + "\n".join(offenders),
        )

    def test_save_manager_stays_headless(self):
        path = ROOT / "managers" / "save_manager.py"
        imports = [module for module, _ in _imports(path)]
        self.assertFalse(
            [m for m in imports if m == "PyQt6" or m.startswith("PyQt6.")],
            "SavegamesManager must not import Qt (dialogs live in the UI layer)",
        )


if __name__ == "__main__":
    unittest.main()
