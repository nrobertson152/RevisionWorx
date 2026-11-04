# SPDX-License-Identifier: GPL-3.0-or-later
"""``revisionworx-gui --self-test REPORT`` (1.0.0): the check CI runs against every packaged build."""
import os
import subprocess
import sys

import pytest

pytest.importorskip("PySide6.QtWidgets")

pytestmark = pytest.mark.qt


def test_self_test_passes_in_a_separate_process(tmp_path):
    report = tmp_path / "report.txt"
    env = {**os.environ, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")}
    proc = subprocess.run([sys.executable, "-m", "revisionworx.gui.app", "--self-test", str(report)],
                          capture_output=True, text=True, timeout=180, env=env)
    text = report.read_text(encoding="utf-8")
    assert proc.returncode == 0, (proc.stderr, text)
    assert text.rstrip().endswith("self-test PASSED"), text
    assert "documents: 1" in text and "opened: True" in text


def test_self_test_reports_failure(tmp_path, monkeypatch):
    """A failing step gives exit code 1 and FAILED, never a false pass."""
    from revisionworx.gui import app, controller
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path / var))      # self_test redirects these; restore afterwards
    monkeypatch.setattr(controller.Controller, "import_document",
                        lambda self, *a, **k: (_ for _ in ()).throw(OSError("injected")))
    report = tmp_path / "report.txt"
    assert app.self_test(report) == 1
    text = report.read_text(encoding="utf-8")
    assert "self-test FAILED" in text and "injected" in text
