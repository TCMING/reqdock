"""Exercise bootstrap before dependency imports in a fresh interpreter."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows platform fallback")
def test_startup_never_queries_wmi_and_keeps_windows_vault():
    code = r"""
import platform
import sys

class UnavailableWmi:
    def exec_query(self, query):
        raise AssertionError("Startup must not query WMI")

platform._wmi = UnavailableWmi()
import api_post
assert platform._wmi is None
assert platform.system() == "Windows"
assert platform.win32_ver()[1]
assert platform.machine()
import keyring
from keyring.backends.Windows import WinVaultKeyring
assert isinstance(keyring.get_keyring(), WinVaultKeyring)
import api_post.desktop
import api_post.__main__
import api_post.skill_cli
assert api_post.__version__ == "0.1.0"
print("bootstrap ready; native Windows Vault retained")
"""
    env = os.environ.copy()
    # Do not let an inherited temporary startup workaround hide a regression.
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert "bootstrap ready" in result.stdout
