from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_FILES = [ROOT / "static" / "js" / "app.js", ROOT / "static" / "js" / "public.js"]


def test_admin_and_public_scripts_parse():
    node = shutil.which("node")
    if not node:
        raise AssertionError("node is required to catch tournament-page script syntax errors")
    for path in JS_FILES:
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path.name} does not parse:\n{result.stderr}"
