# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# PyInstaller spec for the standalone RevisionWorx folder build (Windows and Linux):
#   revisionworx(.exe)      - command line (console)
#   revisionworx-gui(.exe)  - desktop window (no console on Windows)
# Both share one folder of libraries. Build:  pyinstaller packaging/revisionworx.spec --noconfirm
# Icon: none until the Director-supplied logo is committed (docs/REVISIONWORX_BRANDING_DIRECTIVE.md).
import os

block_cipher = None
here = os.path.abspath(SPECPATH)
entry = os.path.join(here, "entry")
excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
            "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia"]

cli_a = Analysis([os.path.join(entry, "cli.py")], pathex=[os.path.dirname(here)], excludes=excludes,
                 cipher=block_cipher)
gui_a = Analysis([os.path.join(entry, "gui.py")], pathex=[os.path.dirname(here)], excludes=excludes,
                 hiddenimports=["revisionworx.gui.qt.mainwindow"], cipher=block_cipher)

cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data, cipher=block_cipher)
gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data, cipher=block_cipher)

cli_exe = EXE(cli_pyz, cli_a.scripts, [], exclude_binaries=True, name="revisionworx", console=True,
              upx=False)
gui_exe = EXE(gui_pyz, gui_a.scripts, [], exclude_binaries=True, name="revisionworx-gui", console=False,
              upx=False)

COLLECT(cli_exe, cli_a.binaries, cli_a.zipfiles, cli_a.datas,
        gui_exe, gui_a.binaries, gui_a.zipfiles, gui_a.datas,
        strip=False, upx=False, name="RevisionWorx")
