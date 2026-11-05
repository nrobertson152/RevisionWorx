# SPDX-License-Identifier: GPL-3.0-or-later
"""Screenshots of the desktop window with realistic sample data, for design review (CI 'screenshots' job).

    QT_QPA_PLATFORM=offscreen python tests/screenshot_gui.py OUTDIR

Not collected by pytest. Uses a throwaway workspace and profile in a temporary folder."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def build(tmp: Path):
    from revisionworx import documents
    from revisionworx.gui.controller import Controller
    from revisionworx.projects import NewProject
    ctl = Controller(cfg_dir=tmp / "cfg", cache_dir=tmp / "cache", state_dir=tmp / "state")
    p = ctl.quick_start(tmp / "Engineering", "Hydraulic Manifold", number="P-1042", customer="Acme Hydraulics")
    for name, number in (("Pump Test Stand", "P-1038"), ("Cylinder Rebuild Kit", "P-1051"),
                         ("Conveyor Guarding", "P-1060")):
        ctl.create_project(NewProject(name=name, number=number))
    src = tmp / "src"
    src.mkdir()
    made = {}
    for fname, number, title in (("manifold_assembly.iam", "MA-1042", "Manifold Assembly"),
                                 ("manifold_block.ipt", "MB-1042-01", "Manifold Block"),
                                 ("manifold_block.idw", "DW-1042-01", "Manifold Block Drawing"),
                                 ("mounting_bracket.ipt", "MB-1042-03", "Mounting Bracket"),
                                 ("port_plate.ipt", "MB-1042-02", "Port Plate"),
                                 ("pressure_test_procedure.docx", "TP-1042-01", "Pressure Test Procedure"),
                                 ("seal_kit_spec.pdf", "SP-1042-01", "Seal Kit Specification")):
        f = src / fname
        f.write_bytes(f"{title} v1".encode())
        made[number] = ctl.import_document(p["id"], f, number=number, title=title)[0]

    def revise(number, text, note):
        d = made[number]
        ctl.checkout(d["id"])
        ctl.working_file(d["id"]).write_bytes(text.encode())
        ctl.checkin_now(d["id"], note)

    revise("MB-1042-01", "block v2", "Added a case drain port")
    revise("MB-1042-01", "block v3", "Deeper counterbore on the P and T ports")
    revise("DW-1042-01", "drawing v2", "Updated for the case drain port")
    with ctl._write() as ws:
        for number, states in (("MB-1042-01", ("in_review", "approved", "released")),
                               ("DW-1042-01", ("in_review", "approved", "released")),
                               ("MB-1042-02", ("in_review",)), ("SP-1042-01", ("in_review", "approved"))):
            rev = documents.history(ws, made[number]["id"])[-1]
            for s in states:
                documents.transition(ws, rev["id"], s, comment="")
    ctl.checkout(made["MA-1042"]["id"])
    ctl.working_file(made["MA-1042"]["id"]).write_bytes(b"assembly edited")
    ctl.working_file(made["MB-1042-03"]["id"]).unlink()
    return ctl, p, made


def main(out: Path) -> int:
    from PySide6.QtWidgets import QApplication
    tmp = Path(tempfile.mkdtemp(prefix="rwx-shots-"))
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        os.environ[var] = str(tmp / "profile" / var)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    from revisionworx.gui.qt.about import AboutDialog
    from revisionworx.gui.qt.mainwindow import MainWindow
    ctl, p, made = build(tmp)
    w = MainWindow(ctl)
    w.resize(1280, 800)
    w.show()
    w.open_location(tmp / "Engineering", quiet=True, select_project=p["id"])
    w.documents_tab.select_document(made["MB-1042-01"]["id"])
    out.mkdir(parents=True, exist_ok=True)
    shots = []

    def shot(widget, name):
        for _ in range(5):
            app.processEvents()
        path = out / f"{name}.png"
        widget.grab().save(str(path))
        shots.append(path)

    for theme_name in ("light", "dark", "graphite", "blueprint"):
        w.set_theme(theme_name)
        shot(w, f"main-{theme_name}")
    w.set_theme("light")
    w.documents_tab.select_document(made["MA-1042"]["id"])
    shot(w, "checked-out")
    w.documents_tab.select_document(made["MB-1042-03"]["id"])
    shot(w, "missing-file")
    about = AboutDialog(w)
    about.show()
    shot(about, "about")
    about.close()
    pal = w.open_palette()
    pal.search.setText("manifold")
    shot(pal, "palette")
    pal.close()
    w.close_ws()
    shot(w, "start")
    print("\n".join(str(s) for s in shots))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "shots")))
