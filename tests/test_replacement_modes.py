"""Run the browser-independent reference-role/routing contract tests."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_replacement_reference_contracts():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for frontend contract tests')
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, '--test', str(root / 'tests/replacement_modes.test.cjs')],
                   cwd=root, check=True, capture_output=True, text=True, timeout=30)
