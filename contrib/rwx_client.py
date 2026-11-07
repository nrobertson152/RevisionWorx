# SPDX-License-Identifier: GPL-3.0-or-later
"""RevisionWorx client for other programs (Caldera, Ledgerbook, OpenSchematix Viewer, TraceMarx, ...).

Copy this single file into your program (it is the reference client for docs/INTEGRATION.md in the
RevisionWorx repository; keep the RWX_CLIENT_VERSION line so copies can be compared). Standard library only,
Python 3.9+, no GUI toolkit. It runs the ``revisionworx`` command (``revisionworx link ...``) and parses its
one-object JSON answer, so it works with a pip install or the standalone build of RevisionWorx, and your
program keeps working, with the integration hidden, when RevisionWorx is not installed.

    from rwx_client import RevisionWorx, RwxError
    rwx = RevisionWorx()                      # finds the revisionworx command
    if rwx.available():
        info = rwx.status("/path/to/file.pdf")          # is this file controlled? what revision?
        rwx.publish("report.pdf", project="J-1", number="CALC-7", title="Pump sizing", message="Initial")

Locating RevisionWorx, first match wins: the ``executable`` argument, the REVISIONWORX_CLI environment variable,
``revisionworx`` on PATH, the standalone build in the usual install folders, then ``python -m revisionworx`` if
the package is importable.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

RWX_CLIENT_VERSION = 3
API_VERSION = 1
_TIMEOUT = 120


class RwxError(Exception):
    """RevisionWorx refused or could not do something. ``kind``: not-found, ambiguous, checked-out, busy, no-workspace,
    bad-input, refused, unavailable, error; and when recording on units: duplicate (an identifier another unit holds),
    changed (the unit changed since you read it), txid-used (that txid was used for something else), conflict (retry with the
    same txid), signature (a field signature that can't be trusted; nothing written). The message is written for the person
    using your program."""

    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def _standalone_candidates() -> List[Path]:
    names = ["revisionworx.exe"] if os.name == "nt" else ["revisionworx"]
    roots: List[Path] = []
    if os.name == "nt":
        for var in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(var):
                roots.append(Path(os.environ[var]))
        roots.append(Path.home() / "Downloads")
    else:
        roots += [Path.home() / "Applications", Path.home() / ".local" / "opt", Path("/opt"), Path.home()]
    out = []
    for root in roots:
        try:
            for folder in sorted(root.glob("RevisionWorx*"), reverse=True):
                for name in names:
                    if (folder / name).is_file():
                        out.append(folder / name)
        except OSError:
            continue
    return out


def find_command(executable: Optional[str] = None) -> Optional[List[str]]:
    """The command prefix that runs RevisionWorx, or None if it cannot be found."""
    for candidate in (executable, os.environ.get("REVISIONWORX_CLI")):
        if candidate:
            return [candidate]
    found = shutil.which("revisionworx")
    if found:
        return [found]
    for exe in _standalone_candidates():
        return [str(exe)]
    try:
        import importlib.util
        if importlib.util.find_spec("revisionworx") is not None and not getattr(sys, "frozen", False):
            return [sys.executable, "-m", "revisionworx"]
    except (ImportError, ValueError):
        pass
    return None


class RevisionWorx:
    """One RevisionWorx installation. ``workspace``: a workspace folder to use for every call (default: the
    file's own workspace, REVISIONWORX_WORKSPACE, or the one most recently opened in RevisionWorx)."""

    def __init__(self, executable: Optional[str] = None, workspace: Optional[str] = None, timeout: int = _TIMEOUT):
        self.command = find_command(executable)
        self.workspace = workspace
        self.timeout = timeout
        self._version: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ plumbing
    def available(self) -> bool:
        """True when RevisionWorx is installed and speaks a compatible integration API."""
        if self.command is None:
            return False
        try:
            v = self.version()
        except RwxError:
            return False
        return v.get("api") == API_VERSION

    def _run(self, *args: str) -> Dict[str, Any]:
        if self.command is None:
            raise RwxError("RevisionWorx is not installed (or not found). Install it, or set the "
                           "REVISIONWORX_CLI environment variable to its 'revisionworx' program.", "unavailable")
        cmd = list(self.command)
        if self.workspace:
            cmd += ["-w", str(self.workspace)]
        cmd += ["link", *[str(a) for a in args]]
        kwargs: Dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000           # CREATE_NO_WINDOW: no console flashes up
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=self.timeout, stdin=subprocess.DEVNULL, **kwargs)
        except FileNotFoundError:
            raise RwxError(f"could not run {cmd[0]}", "unavailable") from None
        except subprocess.TimeoutExpired:
            raise RwxError("RevisionWorx did not answer in time (is the workspace busy or offline?)", "busy") from None
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        try:
            data = json.loads(lines[-1]) if lines else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise RwxError("RevisionWorx gave an unexpected answer" + (f": {detail[-1]}" if detail else ""),
                           "error")
        if not data.get("ok"):
            raise RwxError(data.get("error") or "RevisionWorx refused the request", data.get("kind") or "error")
        return data

    # ------------------------------------------------------------------ calls
    def version(self) -> Dict[str, Any]:
        if self._version is None:
            self._version = self._run("version")
        return self._version

    def workspaces(self) -> List[Dict[str, Any]]:
        return self._run("workspaces")["workspaces"]

    def status(self, file: str) -> Dict[str, Any]:
        """{"controlled": bool, "document": {...} or None, "file": {"state", "matches_latest"}, "workspace": ...}"""
        return self._run("status", os.path.abspath(file))

    def projects(self) -> List[Dict[str, Any]]:
        return self._run("projects")["projects"]

    def documents(self, project: Optional[str] = None, text: str = "") -> List[Dict[str, Any]]:
        args = ["documents"] + (["--project", project] if project else []) + (["--text", text] if text else [])
        return self._run(*args)["documents"]

    def document(self, ref: str, project: Optional[str] = None) -> Dict[str, Any]:
        return self._run("document", ref, *(["--project", project] if project else []))["document"]

    def fetch(self, ref: str, to_dir: str, revision: Optional[str] = None, released: bool = False,
              project: Optional[str] = None, pdf: bool = False) -> Dict[str, Any]:
        """Copy one revision to ``to_dir`` for reading. Returns {"path", "revision": {...}, ...}. ``pdf``: the PDF attached
        to that revision instead (RevisionWorx's ``pdf-derivatives`` feature); a revision without one is ``not-found``."""
        args = ["fetch", ref, "--to", os.path.abspath(to_dir)]
        if revision:
            args += ["--revision", revision]
        if released:
            args.append("--released")
        if project:
            args += ["--project", project]
        if pdf:
            args.append("--pdf")
        return self._run(*args)

    def publish(self, file: str, project: Optional[str] = None, document: Optional[str] = None, number: str = "",
                title: str = "", folder: str = "", message: str = "") -> Dict[str, Any]:
        """New document in ``project``, or a new revision of ``document`` (or of the document numbered ``number``).
        Returns {"result": "created" | "revised" | "unchanged", "document": {...}, ...}."""
        args = ["publish", os.path.abspath(file)]
        for flag, value in (("--project", project), ("--document", document), ("--number", number),
                            ("--title", title), ("--folder", folder), ("--message", message)):
            if value:
                args += [flag, value]
        return self._run(*args)

    def open(self, document: Optional[str] = None, file: Optional[str] = None) -> Dict[str, Any]:
        """Show a document in the RevisionWorx window."""
        args = ["open"] + (["--document", document] if document else []) + (["--file", os.path.abspath(file)]
                                                                             if file else [])
        return self._run(*args)

    def has(self, feature: str) -> bool:
        """True when RevisionWorx announces ``feature`` (``version``'s ``features``). Ask before using a newer command."""
        try:
            return feature in (self.version().get("features") or [])
        except RwxError:
            return False

    # ------------------------------------------------------------------ units (features "units", "unit-records")
    def units(self, project: Optional[str] = None, text: str = "", detail: bool = False) -> List[Dict[str, Any]]:
        args = ["units"] + (["--project", project] if project else []) + (["--text", text] if text else [])
        return self._run(*(args + (["--detail"] if detail else [])))["units"]

    def unit(self, ref: str, at: Optional[int] = None, on: str = "") -> Dict[str, Any]:
        """One unit by id, FieldWorx asset id, serial, tag or other code; ``at`` an event number or ``on`` a date."""
        args = ["unit", ref] + (["--at", str(at)] if at is not None else []) + (["--on", on] if on else [])
        return self._run(*args)["unit"]

    # Recording on units: every call needs a ``txid`` (32 lowercase hex) that you keep and send again with every attempt at
    # the same action, so a retry after a lost answer writes nothing ("result": "already-recorded"). ``if_events``: act only
    # if the unit still has that many events (its ``events.count`` when you decided), else RwxError kind "changed".
    # ``field_signature`` "DEVICE:EVENT:SIG" (feature "field-signatures"): a field record signed on a phone enrolled to an
    # account, accepted with basis="verified" and evidence ["fieldworx-event:EVENT"]; RwxError kind "signature" if it can't be
    # trusted.
    @staticmethod
    def _recording(txid: str, if_events: Optional[int] = None, effective: str = "", basis: Optional[str] = None,
                   reason: str = "", change: str = "", evidence: Iterable[str] = (), field_signature: str = "") -> List[str]:
        args = ["--txid", txid]
        if if_events is not None:
            args += ["--if-events", str(if_events)]
        for flag, value in (("--effective", effective), ("--basis", basis), ("--reason", reason), ("--change", change),
                            ("--field-signature", field_signature)):
            if value:
                args += [flag, value]
        for e in evidence:                  # "KIND:REF" or "KIND:REF=LABEL", e.g. "work-order:WO-00012"
            args += ["--evidence", e]
        return args

    def unit_register(self, txid: str, serials: Iterable[str] = (), issuer: str = "", serial_from: str = "", count: int = 1,
                      tag: str = "", tag_issuer: str = "", other: str = "", name: str = "", model: str = "",
                      cls: str = "product", status: str = "planned", project: str = "", customer: str = "", site: str = "",
                      location: str = "", standard: str = "", documents: Iterable[str] = (),
                      milestones: Optional[Dict[str, str]] = None, notes: str = "", **record: Any) -> Dict[str, Any]:
        """Register units, all or none: one per serial, or ``count`` issued from the numbering ``serial_from``. ``documents``
        are "DOC@REV"; ``milestones`` {"built": "2026-11-05", ...}. Returns {"result", "units": [...]}."""
        args = ["unit-register"]
        for value in serials:
            args += ["--serial", value]
        for flag, value in (("--issuer", issuer), ("--serial-from", serial_from), ("--tag", tag), ("--tag-issuer", tag_issuer),
                            ("--other", other), ("--name", name), ("--model", model), ("--class", cls),
                            ("--status", status), ("--project", project), ("--customer", customer), ("--site", site),
                            ("--location", location), ("--standard", standard), ("--notes", notes)):
            if value:
                args += [flag, value]
        if count != 1:
            args += ["--count", str(count)]
        for ref in documents:
            args += ["--doc", ref]
        for milestone, date in (milestones or {}).items():
            args += [f"--{milestone}", date]
        record.pop("if_events", None)
        return self._run(*(args + self._recording(txid, **record)))

    def unit_fit(self, ref: str, position: str, txid: str, component: str = "", part_number: str = "", serial: str = "",
                 lot: str = "", replace: bool = False, disposition: str = "", **record: Any) -> Dict[str, Any]:
        """Fit a part (with its serial or lot) or a component unit at ``position``; ``replace`` takes out what is there.
        Returns {"result", "unit", "events": [numbers written]}."""
        args = ["unit-fit", ref, position]
        for flag, value in (("--component", component), ("--part-number", part_number), ("--serial", serial), ("--lot", lot),
                            ("--disposition", disposition)):
            if value:
                args += [flag, value]
        if replace:
            args.append("--replace")
        return self._run(*(args + self._recording(txid, **record)))

    def unit_remove(self, ref: str, position: str, txid: str, disposition: str = "", **record: Any) -> Dict[str, Any]:
        args = ["unit-remove", ref, position] + (["--disposition", disposition] if disposition else [])
        return self._run(*(args + self._recording(txid, **record)))

    def unit_note(self, ref: str, text: str, txid: str, **record: Any) -> Dict[str, Any]:
        return self._run(*(["unit-note", ref, text] + self._recording(txid, **record)))

    def unit_milestone(self, ref: str, milestone: str, txid: str, on: str = "", **record: Any) -> Dict[str, Any]:
        """built, shipped, installed, commissioned or decommissioned, ``on`` a date ("" when unknown)."""
        args = ["unit-milestone", ref, milestone] + (["--on", on] if on else [])
        return self._run(*(args + self._recording(txid, **record)))

    def unit_change_install(self, ref: str, order: str, txid: str, **record: Any) -> Dict[str, Any]:
        """The change order ``order`` was made on the unit: its drawings move to the order's new revisions, reported unless
        ``basis="verified"``. Returns {"result", "unit", "events", "compliance"}."""
        return self._run(*(["unit-change-install", ref, order] + self._recording(txid, **record)))


    # ------------------------------------------------------------------ phones (feature "field-signatures")
    def devices(self) -> List[Dict[str, Any]]:
        """Phones enrolled to accounts in a team workspace, revoked ones too; ``active`` says whether they count now."""
        return self._run("devices")["devices"]

    def device_enrol(self, account: str, device: str, key: str, fingerprint: str, name: str = "") -> Dict[str, Any]:
        """Enrol a phone to an account, as the administrator logged in on this computer. ``fingerprint`` is what the person
        typed from the phone's screen. Returns {"result": "enrolled" | "already-enrolled", "device": {...}}."""
        args = ["device-enrol", "--account", account, "--device", device, "--key", key, "--fingerprint", fingerprint]
        return self._run(*(args + (["--name", name] if name else [])))


def describe(status: Dict[str, Any]) -> str:
    """One line for a status bar: 'DWG-1 rev B · Released · checked out by you', or '' for an uncontrolled file."""
    if not status.get("controlled"):
        return ""
    doc = status["document"]
    latest = doc.get("latest") or {}
    parts = [f"{doc.get('number') or doc.get('title') or 'Document'} rev {latest.get('label', '?')}",
             latest.get("state_name", "")]
    co = doc.get("checkout")
    if co:
        parts.append("checked out by you" if co.get("mine") else f"checked out by {co.get('user', '?')}")
    state = (status.get("file") or {}).get("state")
    if state in ("modified", "modified-externally"):
        parts.append("this file differs from the latest revision")
    elif state == "missing":
        parts.append("file missing")
    return " · ".join(p for p in parts if p)
