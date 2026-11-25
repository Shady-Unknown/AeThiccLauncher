import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIRS = ("core", "managers", "network", "ui")
TARGET_FILES = (ROOT / "main.py",)


def _print_calls(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]


class NoPrintTests(unittest.TestCase):
    def test_application_code_uses_logging_not_print(self):
        offenders = []
        for directory in TARGET_DIRS:
            for path in (ROOT / directory).rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                for lineno in _print_calls(path):
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
        for path in TARGET_FILES:
            for lineno in _print_calls(path):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")

        self.assertEqual(
            offenders, [],
            "Use logging.getLogger(__name__) instead of print():\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
