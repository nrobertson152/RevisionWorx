# SPDX-License-Identifier: GPL-3.0-or-later
"""`revisionworx doctor`: one fixture per kind of damage, an exact expected code, and proof that nothing was changed."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

from revisionworx import atomicio, cli, doctor, documents as D, projects
from revisionworx.projects import NewProject
from revisionworx.workspace import REV_FIELD, RecordDelete, RecordPut, Workspace, WorkspaceCorruptError, entry_digest, journal_gaps


def tree(root: Path) -> dict:
    """Every file and folder under root with its bytes and modification time."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            stat = path.stat()
            snap[str(path)] = (stat.st_mtime_ns, path.read_bytes() if path.is_file() else None)
    return snap


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "share"
    (root / "Engineering").mkdir(parents=True)
    with Workspace.create(root, "doctor") as ws:
        projects.add_root(ws, "Engineering", root / "Engineering")
        project = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
        path = projects.project_folder(ws, project) / "02 CAD" / "Parts" / "bracket.ipt"
        path.write_bytes(b"v1")
        doc, rev = D.register_document(ws, project["id"], path)
        co = D.checkout(ws, doc["id"])
        path.write_bytes(b"v2")
        rev2 = D.checkin(ws, doc["id"], co["token"], "second")
    return {"root": root, "meta": root / ".revisionworx", "doc": doc["id"], "rev": rev, "rev2": rev2, "path": path}


def codes(world) -> list[str]:
    before = tree(world["root"])
    report = doctor.examine(world["root"])
    assert tree(world["root"]) == before, "doctor changed something"
    return sorted({f["code"] for f in report["findings"]})


def last_entry(world) -> Path:
    return sorted((world["meta"] / "journal").glob("*.json"))[-1]


def test_a_healthy_workspace_has_no_findings(world):
    report = doctor.examine(world["root"])
    assert report["ok"] and report["findings"] == [] and report["skipped"] == []
    assert report["checked"] == ["identity", "journal", "locks", "records", "documents", "history", "content (hashed)"]


def _content_path(world, rev) -> Path:
    return world["meta"] / "content" / "sha256" / rev["sha256"][:2] / rev["sha256"]


def _rewrite_entry(world, seq, change):
    path = world["meta"] / "journal" / f"{seq:020d}.json"
    entry = atomicio.read_json(path)
    change(entry)
    entry["digest"] = entry_digest(entry)
    atomicio.atomic_write_json(path, entry)


def _forge(world, change_rev):
    """Rewrite a revision through the journal, as a bug in a caller could."""
    with Workspace.open(world["root"], writable=True) as ws:
        change_rev(ws, world["rev"]["id"])


def _rewrite_revision(ws, rid):
    rev = ws.get("revision", rid)
    forged = {k: v for k, v in rev.items() if k != REV_FIELD}
    forged["description"] = "rewritten"
    ws.commit("bug", [RecordPut("revision", rid, forged, expect_rev=rev[REV_FIELD])])


def _rewrite_transitions(ws, rid):
    D.transition(ws, rid, "in_review")
    rev = ws.get("revision", rid)
    forged = {k: v for k, v in rev.items() if k != REV_FIELD}
    forged["transitions"] = []
    ws.commit("bug", [RecordPut("revision", rid, forged, expect_rev=rev[REV_FIELD])])


def _delete_revision(ws, rid):
    ws.commit("bug", [RecordDelete("revision", rid, expect_rev=ws.get("revision", rid)[REV_FIELD])])


def _put(ws, kind, rid, change=None, value=None):
    current = ws.get(kind, rid)
    body = {k: v for k, v in (current or {}).items() if k != REV_FIELD} if value is None else value
    if change:
        body.update(change)
    ws.commit("bug", [RecordPut(kind, rid, body, expect_rev=current[REV_FIELD] if current else 0)])


def _forged_put(kind, rid_of, change=None, value=None):
    def damage(w):
        with Workspace.open(w["root"], writable=True) as ws:
            _put(ws, kind, rid_of(w), change, value)
    return damage


def _strip_digest(w):
    path = last_entry(w)
    entry = atomicio.read_json(path)
    del entry["digest"]
    atomicio.atomic_write_json(path, entry)


def _revoked_lock_left_behind(w):
    ws = Workspace.open(w["root"], writable=True)
    lock = w["meta"] / "locks" / "writer.lock"
    kept = lock.read_bytes()
    Workspace.break_writer_lock(w["root"], ws._lock_token, "unresponsive")
    ws._lock_token = None
    ws.close()
    for evidence in (w["meta"] / "locks").glob("writer.lock.*"):
        evidence.unlink()
    lock.write_bytes(kept)


def _altered_evidence(w):
    (w["meta"] / "locks" / ".fencing-aa").write_bytes(b"\x00")
    Workspace.clear_orphaned_release(w["root"], ".fencing-aa", "writer stopped")
    with Workspace.open(w["root"], writable=True):
        pass
    next((w["meta"] / "locks").glob("writer.lock.orphaned-*")).write_bytes(b"changed")


def _deleted_but_present(w):
    rid = "root_" + "e" * 32
    with Workspace.open(w["root"], writable=True) as ws:
        ws.commit("t", [RecordPut("root", rid, {"name": "x"}, expect_rev=0)])
        kept = _record(w, "root", rid).read_bytes()
        ws.commit("t", [RecordDelete("root", rid, expect_rev=ws.get("root", rid)[REV_FIELD])])
    _record(w, "root", rid).write_bytes(kept)


def _blob_gone(w):
    with Workspace.open(w["root"], writable=True) as ws:
        ref = ws.write_blob("baseline", {"x": 1})
        ws.commit("t", [RecordPut("root", "root_" + "b" * 32, {"name": "x"}, expect_rev=0)], blob_refs=[ref])
    kind, blob_id = ref.split("/", 1)
    (w["meta"] / "blobs" / kind / f"{blob_id}.json").unlink()


def _record(world, kind, rid) -> Path:
    return world["meta"] / "records" / kind / f"{rid}.json"


DAMAGE = {
    "W001 no identity": (lambda w: (w["meta"] / "workspace.json").unlink(), ["W001"]),
    "W002 bad identity": (lambda w: (w["meta"] / "workspace.json").write_bytes(b"\x00"), ["W002"]),
    "W003 newer format": (lambda w: atomicio.atomic_write_json(
        w["meta"] / "workspace.json", {"format": "RevisionWorx Workspace", "format_version": 9999}), ["W003"]),
    "J001 gap": (lambda w: sorted((w["meta"] / "journal").glob("*.json"))[2].unlink(), ["J001"]),
    "J002 torn entry": (lambda w: last_entry(w).write_bytes(b'{"seq": 1'), ["J002"]),
    "J002 altered entry": (lambda w: last_entry(w).write_bytes(last_entry(w).read_bytes().replace(b"second", b"SECOND")), ["J002"]),
    "J002 not an object": (lambda w: last_entry(w).write_bytes(b"null"), ["J002"]),
    "J004 stray file": (lambda w: (w["meta"] / "journal" / "notes.txt").write_bytes(b"x"), ["J004"]),
    "S001 bad state": (lambda w: (w["meta"] / "state.json").write_bytes(b"\x00"), ["S001", "S003"]),
    "S002 state ahead": (lambda w: atomicio.atomic_write_json(w["meta"] / "state.json", {"applied_seq": 9999}), ["S002"]),
    "S003 pending": (lambda w: atomicio.atomic_write_json(w["meta"] / "state.json", {"applied_seq": 1}), ["S003"]),
    "L001 held": (lambda w: atomicio.atomic_write_json(w["meta"] / "locks" / "writer.lock", {"token": "a" * 32, "user": "u"}), ["L001"]),
    "L003 unreadable lock": (lambda w: (w["meta"] / "locks" / "writer.lock").write_bytes(b"\x00"), ["L003"]),
    "L004 orphan": (lambda w: atomicio.atomic_write_json(w["meta"] / "locks" / ".releasing-aa", {"token": "a" * 32}), ["L004"]),
    "L005 unreadable orphan": (lambda w: (w["meta"] / "locks" / ".fencing-aa").write_bytes(b"\x00"), ["L005"]),
    "L006 clear in flight": (lambda w: (w["meta"] / "locks" / ".clearing-aa-bb").write_bytes(b"x"), ["L006"]),
    "L007 unfenced evidence": (lambda w: atomicio.atomic_write_json(w["meta"] / "locks" / "writer.lock.broken-aa", {"token": "a" * 32}), ["L007"]),
    "R001 record differs": (lambda w: atomicio.atomic_write_json(
        _record(w, "document", w["doc"]), {**atomicio.read_json(_record(w, "document", w["doc"])), "title": "x"}), ["R001"]),
    "R002 record missing": (lambda w: _record(w, "document", w["doc"]).unlink(), ["R002"]),
    "R003 not in journal": (lambda w: atomicio.atomic_write_json(_record(w, "root", "root_" + "f" * 32), {"name": "r"}), ["R003"]),
    "R005 record unreadable": (lambda w: _record(w, "document", w["doc"]).write_bytes(b"\x00"), ["R005"]),
    "C001 content missing": (lambda w: _content_path(w, w["rev"]).unlink(), ["C001"]),
    "C002 content damaged": (lambda w: _content_path(w, w["rev"]).write_bytes(b"bad"), ["C002"]),
    "C004 unused content": (lambda w: (_content_path(w, w["rev"]).parent / ("0" * 64)).write_bytes(b"x"), ["C004"]),
    "C005 interrupted store": (lambda w: (w["meta"] / "content" / "sha256" / ".incoming.x.tmp").write_bytes(b"x"), ["C005"]),
    "B001 blob gone": (_blob_gone, ["B001"]),
    "S001 no state": (lambda w: (w["meta"] / "state.json").unlink(), ["S001", "S003"]),
    "J003 no digest": (_strip_digest, ["J003"]),
    "L002 revoked lock left": (_revoked_lock_left_behind, ["L002", "S003"]),
    "L008 evidence altered": (_altered_evidence, ["L008"]),
    "R004 deleted but present": (_deleted_but_present, ["R004"]),
    "D003 checkout of nothing": (_forged_put("checkout", lambda w: "doc_" + "e" * 32, value={"document_id": "doc_" + "e" * 32, "token": "t"}), ["D003"]),
    "D005 document without project": (_forged_put("document", lambda w: w["doc"], {"project_id": "prj_" + "e" * 32}), ["D005"]),
    "D006 released revision missing": (_forged_put("document", lambda w: w["doc"], {"released_rev_id": "rev_" + "e" * 32}), ["D006"]),
    "D010 revision rewritten": (lambda w: _forge(w, _rewrite_revision), ["D010"]),
    "D011 transitions rewritten": (lambda w: _forge(w, _rewrite_transitions), ["D011"]),
    "D012 revision deleted": (lambda w: _forge(w, _delete_revision), ["C004", "D001", "D012"]),
}


@pytest.mark.parametrize("name", list(DAMAGE))
def test_each_kind_of_damage_has_its_code(world, name):
    damage, expected = DAMAGE[name]
    damage(world)
    assert codes(world) == expected


def test_a_revision_without_its_document(world):
    # Moving a revision to another document is itself a history violation, so both are expected.
    _forged_put("revision", lambda w: w["rev"]["id"], {"document_id": "doc_" + "e" * 32})(world)
    assert {"D004", "D010"} <= set(codes(world))


def test_a_state_the_lifecycle_does_not_have(world):
    _forged_put("revision", lambda w: w["rev"]["id"], {"state": "nonsense"})(world)
    assert "D007" in codes(world)


def test_content_that_cannot_be_read(world, monkeypatch):
    def denied(path):
        raise PermissionError("denied")
    monkeypatch.setattr(doctor, "hash_file", denied)
    assert codes(world) == ["C003"]


def test_every_code_in_the_module_is_documented_and_has_a_test():
    source = Path(doctor.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r'"([A-Z]\d{3})"', source))
    documented = set(re.findall(r"^\| `([A-Z]\d{3})`", (Path(__file__).parent.parent / "docs" / "CORE_API.md").read_text(encoding="utf-8"), re.M))
    assert used == documented
    tests = Path(__file__).read_text(encoding="utf-8")
    assert not {c for c in used if f'"{c}"' not in tests}


def _team(tmp_path, monkeypatch):
    """A team workspace, examined from the computer that made it (ADR-024)."""
    from revisionworx import clientstate
    from team_helpers import new_team
    folder, pc, _code, boss = new_team(tmp_path)
    monkeypatch.setattr(clientstate, "default_state_dir", lambda: pc)
    with Workspace.open(folder, writable=True, session=boss, state_dir=pc) as ws:
        ws.commit("t", [RecordPut("project", "prj_" + "a" * 32, {"v": 1})])
        head = ws.head_seq()
    return folder, boss, pc, head


def _codes(report) -> set:
    return {f["code"] for f in report["findings"]}


def test_a_healthy_team_workspace(tmp_path, monkeypatch):
    folder, boss, pc, _head = _team(tmp_path, monkeypatch)
    report = doctor.examine(folder)
    assert report["ok"] and "team" in report["checked"] and not _codes(report)
    assert not (set(report["checked"]) & {"locks"})                # its lock protocol is a different one


def test_a_team_entry_lost_from_the_folder(tmp_path, monkeypatch):
    folder, _boss, _pc, head = _team(tmp_path, monkeypatch)
    (folder / ".revisionworx" / "journal" / f"{head:020d}.json").unlink()
    assert {"J010", "T001"} <= _codes(doctor.examine(folder))


def test_a_team_entry_altered_in_the_folder(tmp_path, monkeypatch):
    folder, _boss, _pc, head = _team(tmp_path, monkeypatch)
    path = folder / ".revisionworx" / "journal" / f"{head:020d}.json"
    entry = atomicio.read_json_object(path)
    entry["time"] = "1999"
    entry["digest"] = entry_digest(entry)
    atomicio.atomic_write_json(path, entry)
    assert {"J011", "T001"} <= _codes(doctor.examine(folder))


def test_a_next_team_entry_that_doesnt_follow_the_one_before(tmp_path, monkeypatch):
    folder, _boss, _pc, head = _team(tmp_path, monkeypatch)
    journal = folder / ".revisionworx" / "journal"
    entry = {**atomicio.read_json_object(journal / f"{head:020d}.json"), "seq": head + 1, "prev": "not the entry before"}
    entry["digest"] = entry_digest(entry)
    atomicio.atomic_write_json(journal / f"{head + 1:020d}.json", entry)
    assert {"J012", "T001"} <= _codes(doctor.examine(folder))


def test_a_damaged_next_team_entry(tmp_path, monkeypatch):
    folder, _boss, _pc, head = _team(tmp_path, monkeypatch)
    (folder / ".revisionworx" / "journal" / f"{head + 1:020d}.json").write_text("nonsense", encoding="utf-8")
    assert {"J002", "T001"} <= _codes(doctor.examine(folder))


def test_a_team_workspace_with_no_administrator_is_reported(tmp_path, monkeypatch):
    """The journal's rules should make this impossible, which is why it's worth saying if it ever happens."""
    from revisionworx.shared import SharedWorkspace
    folder, _boss, _pc, _head = _team(tmp_path, monkeypatch)
    real = SharedWorkspace.list
    monkeypatch.setattr(SharedWorkspace, "list", lambda self, kind: {} if kind == "account" else real(self, kind))
    assert "T002" in _codes(doctor.examine(folder))


def test_someone_taking_a_turn_is_only_information(tmp_path, monkeypatch):
    folder, boss, pc, _head = _team(tmp_path, monkeypatch)
    with Workspace.open(folder, writable=True, session=boss, state_dir=pc):
        report = doctor.examine(folder)
    assert "T003" in _codes(report) and report["ok"]


def test_a_team_workspace_that_cant_be_opened_is_a_finding(tmp_path, monkeypatch):
    from revisionworx import shared
    folder, _boss, _pc, _head = _team(tmp_path, monkeypatch)

    def refuses(*_args, **_kwargs):
        raise OSError(13, "permission denied")
    monkeypatch.setattr(shared.SharedWorkspace, "attach", classmethod(refuses))
    assert "T001" in _codes(doctor.examine(folder))


def _stray_high_entry(world) -> Path:
    path = world["meta"] / "journal" / "00000000010000000000.json"
    path.write_text("{}", encoding="utf-8")
    return path


def test_a_stray_high_numbered_file_is_one_finding_not_ten_billion(world):
    _stray_high_entry(world)
    report = doctor.examine(world["root"])
    gaps = [f for f in report["findings"] if f["code"] == "J001"]
    assert len(gaps) == 1 and "to 9999999999" in gaps[0]["subject"]
    assert not any(f["code"] == "S003" for f in report["findings"]), "those are not saved changes waiting"


def test_a_stray_high_numbered_file_refuses_opens_quickly_and_moving_it_recovers(world):
    stray = _stray_high_entry(world)
    for writable in (False, True):
        with pytest.raises(WorkspaceCorruptError, match="missing entries"):
            Workspace.open(world["root"], writable=writable)
    stray.unlink()
    with Workspace.open(world["root"], writable=True):
        pass
    assert doctor.examine(world["root"])["ok"]


def test_an_entry_numbered_zero_is_refused_by_name(world):
    (world["meta"] / "journal" / ("0" * 20 + ".json")).write_text("{}", encoding="utf-8")
    with pytest.raises(WorkspaceCorruptError, match="numbered 0"):
        Workspace.open(world["root"])
    assert "J006" in codes(world)


@pytest.mark.parametrize("seqs,gaps", [([], []), ([1], []), ([1, 2, 3], []), ([2], [(1, 1)]), ([1, 3, 6], [(2, 2), (4, 5)]),
                                       ([1, 10 ** 19], [(2, 10 ** 19 - 1)])])
def test_journal_gaps(seqs, gaps):
    assert journal_gaps(seqs) == gaps


def test_one_bad_entry_does_not_hide_a_second(world):
    entries = sorted((world["meta"] / "journal").glob("*.json"))
    entries[1].write_bytes(b"null")
    entries[-1].write_bytes(b"null")
    report = doctor.examine(world["root"])
    assert [f["subject"] for f in report["findings"] if f["code"] == "J002"] == ["journal entry 2", f"journal entry {len(entries)}"]
    assert report["skipped"], "what could not be judged must be said"


def test_fenced_entry_is_information_and_the_workspace_is_ok(world):
    ws = Workspace.open(world["root"], writable=True)
    real = ws._current_rev

    def revoke(kind, rid):
        ws._current_rev = real
        Workspace.break_writer_lock(world["root"], ws._lock_token, "unresponsive")
        return real(kind, rid)
    ws._current_rev = revoke
    with pytest.raises(Exception, match="fenced"):
        ws.commit("t", [RecordPut("root", "root_" + "c" * 32, {"name": "x"}, expect_rev=0)])
    ws._lock_token = None
    ws.close()
    report = doctor.examine(world["root"])
    assert "J005" in [f["code"] for f in report["findings"]] and report["ok"]


def test_quick_skips_hashing_and_says_so(world):
    _content_path(world, world["rev"]).write_bytes(b"bad")
    report = doctor.examine(world["root"], hash_content=False)
    assert report["ok"] and report["skipped"] == ["hashing content objects: --quick"]
    assert "content (present only)" in report["checked"]


def test_a_workspace_in_use_is_examined_without_disturbing_the_writer(world):
    with Workspace.open(world["root"], writable=True) as ws:
        token = ws._lock_token
        report = doctor.examine(world["root"])
        assert [f["code"] for f in report["findings"]] == ["L001"] and report["ok"]
        assert ws.commit("t", [RecordPut("root", "root_" + "c" * 32, {"name": "x"}, expect_rev=0)])
        assert Workspace.read_writer_lock(world["root"])["token"] == token


def test_cli_exit_codes_and_json(world, capsys):
    import json
    assert cli.main(["-w", str(world["root"]), "doctor"]) in (0, None)
    assert "healthy" in capsys.readouterr().out
    _content_path(world, world["rev"]).write_bytes(b"bad")
    with pytest.raises(SystemExit) as exc:
        cli.main(["-w", str(world["root"]), "--json", "doctor"])
    assert exc.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["errors"] == 1 and report["findings"][0]["code"] == "C002"
    assert report["findings"][0]["action"]


def test_every_problem_says_what_to_do(world):
    (world["meta"] / "locks" / "writer.lock").write_bytes(b"\x00")
    _content_path(world, world["rev"]).unlink()
    report = doctor.examine(world["root"])
    assert report["errors"] == 2 and all(f["action"] for f in report["findings"] if f["severity"] != "info")


def test_a_lock_broken_between_two_checks_is_not_called_unfenced(world, monkeypatch):
    """The token list was read, then the lock was revoked and archived, then the archive was seen."""
    ws = Workspace.open(world["root"], writable=True)
    token, real = ws._lock_token, Workspace._revoked_tokens
    fired = []

    def racing(meta):
        tokens = real(meta)
        if not fired:
            fired.append(True)
            Workspace.break_writer_lock(world["root"], token, "unresponsive")
        return tokens
    monkeypatch.setattr(Workspace, "_revoked_tokens", staticmethod(racing))
    report = doctor.examine(world["root"])
    ws._lock_token = None
    ws.close()
    assert fired and report["errors"] == 0, report["findings"]
    assert report["skipped"] == []


def test_a_document_created_between_two_checks_is_not_called_dangling(world, monkeypatch):
    """Documents were listed, then a document and its revision were saved, then revisions were listed."""
    real = Workspace.list
    fired = []

    def racing(self, kind):
        listed = real(self, kind)
        if kind == "document" and not fired:
            fired.append(True)
            with Workspace.open(world["root"], writable=True) as writer:
                extra = world["path"].with_name("extra.ipt")
                extra.write_bytes(b"x")
                D.register_document(writer, next(iter(real(writer, "project"))), extra)
        return listed
    monkeypatch.setattr(Workspace, "list", racing)
    report = doctor.examine(world["root"])
    assert fired and report["errors"] == 0, report["findings"]


def test_a_workspace_that_never_settles_says_so(world, monkeypatch):
    n = iter(range(1000))
    monkeypatch.setattr(doctor, "_head", lambda meta: next(n))
    report = doctor.examine(world["root"])
    assert any("consistent view" in s for s in report["skipped"])
    assert report["ok"] is False and report["settled"] is False and "inconclusive" in doctor.render(report)


def test_real_writer_never_produces_a_false_error(world):
    stop, failures = threading.Event(), []

    def writer():
        i = 0
        while not stop.is_set():
            i += 1
            try:
                ws = Workspace.open(world["root"], writable=True)
                project = next(iter(ws.list("project")))
                path = world["path"].with_name(f"w{i}.ipt")
                path.write_bytes(str(i).encode())
                D.register_document(ws, project, path)
                if i % 3 == 0:
                    Workspace.break_writer_lock(world["root"], ws._lock_token, "unresponsive")
                    ws._lock_token = None
                ws.close()
            except Exception as exc:                 # noqa: BLE001 - reported below
                failures.append(repr(exc))
                return
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        reports, deadline = [], time.monotonic() + 4
        while time.monotonic() < deadline:
            reports.append(doctor.examine(world["root"]))
    finally:
        stop.set()
        thread.join()
    assert not failures
    for report in reports:
        settled = not any("consistent view" in s for s in report["skipped"])
        assert not settled or report["errors"] == 0, report["findings"]
    stop.set()
    assert doctor.examine(world["root"])["errors"] == 0, "and it must settle once the writer stops"


def test_something_unreadable_is_reported_and_the_rest_still_runs(world, monkeypatch):
    def boom(*a):
        raise PermissionError("denied")
    monkeypatch.setattr(doctor, "_check_locks", boom)
    report = doctor.examine(world["root"])
    assert [f["code"] for f in report["findings"]] == ["X001"] and report["skipped"] == ["locks"]
    assert "documents" in report["checked"]


@pytest.mark.parametrize("damage", ["writer.lock", ".fencing-aa"])
def test_following_the_advice_gets_the_workspace_back(world, damage):
    """The commands the doctor recommends for a lost lock file are real and work."""
    locks = world["meta"] / "locks"
    (locks / damage).write_bytes(b"\x00")
    root = str(world["root"])
    if damage == "writer.lock":
        # This moves the file aside and stops short of unblocking anyone, saying that the audited clear comes next.
        assert cli.main(["-w", root, "lock", "break", "--token", "?", "--reason", "stopped"]) == 2
    assert [f["code"] for f in doctor.examine(root)["findings"]] == ["L005"]
    name = next(p.name for p in locks.iterdir() if p.name.startswith((".fencing-", ".releasing-")))
    assert cli.main(["-w", root, "lock", "clear-orphan", name, "--reason", "writer stopped"]) == 0
    report = doctor.examine(root)
    assert report["ok"], report["findings"]
    with Workspace.open(root, writable=True):
        pass


def test_the_records_advice_works(world, capsys):
    atomicio.atomic_write_json(_record(world, "document", world["doc"]), {"title": "wrong"})
    root = str(world["root"])
    assert doctor.examine(root)["findings"][0]["code"] == "R001"
    assert cli.main(["-w", root, "verify", "--repair"]) == 0
    assert doctor.examine(root)["ok"]


def test_the_commands_it_recommends_exist():
    parser = cli.build_parser()
    for argv in (["doctor"], ["lock", "show"], ["lock", "break", "--token", "?", "--reason", "x"], ["doc", "gc"],
                 ["lock", "clear-orphan", "f", "--reason", "x"], ["verify", "--repair"], ["doc", "release-checkout", "--help"]):
        try:
            parser.parse_args(argv)
        except SystemExit as exc:                    # --help
            assert exc.code == 0


def test_a_busy_release_file_is_a_warning_not_damage(world, monkeypatch):
    """Windows CI 41940970385: a live writer's in-flight .releasing-* file refused reads for a moment and doctor
    called it L005 (unreadable, writers refused), although writers treat exactly this as busy (ADR-019 rev. 5)."""
    locks = world["root"] / ".revisionworx" / "locks"
    busy = locks / (".releasing-" + "b" * 32)
    busy.write_text(json.dumps({"token": "t" * 32, "user": "u", "host": "h", "pid": 1, "acquired": "x"}))
    real = Workspace._read_holder

    def refuse(path):
        if path == busy:
            raise PermissionError(13, "sharing violation", str(path))
        return real(path)
    monkeypatch.setattr(Workspace, "_read_holder", staticmethod(refuse))
    report = doctor.examine(world["root"])
    codes = {f["code"]: f["severity"] for f in report["findings"] if busy.name in f["subject"]}
    assert codes == {"L009": "warning"}
    damaged = locks / (".releasing-" + "d" * 32)
    damaged.write_text("{not json")
    report = doctor.examine(world["root"])
    assert any(f["code"] == "L005" and damaged.name in f["subject"] and f["severity"] == "error"
               for f in report["findings"])


def test_a_release_file_that_vanishes_while_examined_is_ignored(world, monkeypatch):
    locks = world["root"] / ".revisionworx" / "locks"
    gone = locks / (".releasing-" + "g" * 32)
    gone.write_text("{}")
    real = Workspace._read_holder

    def finish(path):
        if path == gone:
            gone.unlink()
            raise FileNotFoundError(2, "gone", str(path))
        return real(path)
    monkeypatch.setattr(Workspace, "_read_holder", staticmethod(finish))
    report = doctor.examine(world["root"])
    assert not [f for f in report["findings"] if gone.name in f["subject"]]
