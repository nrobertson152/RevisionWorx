# SPDX-License-Identifier: GPL-3.0-or-later
"""Kill a real process before every filesystem change of every core operation, then recover in another one.

For each operation the victim runs in its own process and dies with ``os._exit`` before change number 0, 1, 2...
until it finally completes, so a test covers every crash point there is rather than a chosen few. After each death
the lock is broken if one was left, a new process recovers, and the workspace is checked against the journal:

* every read equals a replay of the effective journal entries, and verify() finds nothing;
* every revision's content is present and hashes correctly; every revision chain is whole;
* nothing is left that blocks writers, and a new writer gets in;
* the search index agrees with the journal;
* the operation happened completely or not at all, and doing it again ends in the right state.

Process death only: no claim about power loss, where unsynced data can also vanish, or about SMB/NFS.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from proc_harness import Actor, Result
from revisionworx import atomicio, documents as D, projects
from revisionworx.content import ContentStore
from revisionworx.index import open_index
from revisionworx.projects import NewProject
from revisionworx.workspace import KINDS, REV_FIELD, RecordPut, Workspace

TXID = "ab" * 16
MAX_CRASH_POINTS = 300


def _rmtree(path: Path) -> None:
    """A scanner can hold a file briefly on Windows right after the writer has gone."""
    for attempt in range(100):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.05)


@dataclass
class World:
    """A workspace built once, then restored to the same paths before each crash point."""
    base: Path
    root: Path
    project: str = ""
    folder: Path | None = None
    doc: str = ""
    rev: str = ""
    path: Path | None = None
    token: str = ""
    runs: int = 0

    @property
    def meta(self) -> Path:
        return self.root / ".revisionworx"

    def snapshot(self) -> None:
        shutil.copytree(self.root, self.base / "snapshot")

    def reset(self) -> Path:
        _rmtree(self.root)
        if (self.base / "snapshot").exists():
            shutil.copytree(self.base / "snapshot", self.root)
        self.runs += 1
        ctl = self.base / f"ctl{self.runs}"
        ctl.mkdir()
        return ctl

    def cache(self) -> Path:
        return self.base / f"cache{self.runs}"


def build(tmp_path: Path, checked_out=False, edited=None, prepare=None) -> World:
    root = tmp_path / "share"
    (root / "Engineering").mkdir(parents=True)
    w = World(base=tmp_path, root=root)
    with Workspace.create(root, "crash matrix") as ws:
        projects.add_root(ws, "Engineering", root / "Engineering")
        record = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
        w.project, w.folder = record["id"], projects.project_folder(ws, record)
        w.path = w.folder / "02 CAD" / "Parts" / "bracket.ipt"
        w.path.write_bytes(b"IPT v1")
        doc, rev = D.register_document(ws, w.project, w.path)
        w.doc, w.rev = doc["id"], rev["id"]
        if checked_out:
            w.token = D.checkout(ws, w.doc)["token"]
        if edited is not None:
            w.path.write_bytes(edited)
        if prepare:
            prepare(w, ws)
    w.snapshot()
    return w


# ---------------------------------------------------------------------------------------- the common oracle
def replay(ws: Workspace) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for entry in ws.entries_after(0):
        for item in entry["records"]:
            records = state.setdefault(item["kind"], {})
            if item.get("delete"):
                records.pop(item["id"], None)
            else:
                records[item["id"]] = {**item["data"], REV_FIELD: entry["seq"]}
    return state


def assert_consistent(w: World, context: str, allowed_problems=()) -> None:
    __tracebackhide__ = True
    locks = w.meta / "locks"
    assert not list(locks.glob(".releasing-*")) and not list(locks.glob(".fencing-*")) \
        and not list(locks.glob(".clearing-*")), f"{context}: something still blocks writers"
    assert Workspace.read_writer_lock(w.root) is None, f"{context}: lock left behind"
    with Workspace.open(w.root) as ws:
        assert ws.applied_seq() == ws.head_seq(), f"{context}: recovery did not finish"
        truth = replay(ws)
        for kind in sorted(KINDS):
            assert ws.list(kind) == truth.get(kind, {}), f"{context}: list({kind}) disagrees with the journal"
        problems = [p for p in ws.verify()["problems"] if p["problem"] not in allowed_problems]
        assert not problems, f"{context}: verify found {problems}"
        store = ContentStore(ws)
        for doc in ws.list("document").values():
            chain = D.revisions_of(ws, doc["id"])
            assert len(chain) == doc["rev_count"], f"{context}: revision chain of {doc['rel_path']}"
            for rev in chain:
                assert store.verify(rev["sha256"]), f"{context}: content of revision {rev['label']} lost or damaged"
        for rid in ws.list("checkout"):
            assert ws.get("document", rid) is not None, f"{context}: checkout of a missing document"
        with open_index(ws, w.cache()) as idx:
            assert idx.cursor_seq() == ws.head_seq(), f"{context}: index cursor"
            assert {d["id"] for d in idx.documents()} == set(ws.list("document")), f"{context}: index documents"
            assert {r[0] for r in idx.db.execute("SELECT document_id FROM checkouts")} == set(ws.list("checkout")), \
                f"{context}: index checkouts"


def recover(w: World, ctl: Path, context: str) -> None:
    """What an administrator and the next user would do: break a lock nobody owns, then open it."""
    holder = Workspace.read_writer_lock(w.root)
    steps = [{"do": "open"}, {"do": "close"}]
    if holder:
        steps.insert(0, {"do": "break_lock", "token": holder["token"]})
    result = Actor(ctl, "recovery", w.root, steps).run()
    assert result.clean, f"{context}: recovery failed: {result.steps} {result.stderr}"


def crash_matrix(w: World, steps: list[dict], after_crash, after_clean=None, recovering=True, min_points=1) -> int:
    """Run ``steps`` with a death before change 0, 1, 2... Returns the number of crash points covered."""
    k = 0
    while True:
        ctl = w.reset()
        result: Result = Actor(ctl, "victim", w.root, steps, die_at=k).run()
        assert not result.unexpected(), f"{k}: {result.unexpected()}"
        if not result.killed:
            assert result.exit_code == 0, f"{k}: {result.stderr}"
            assert all(s["ok"] for s in result.steps), f"{k}: {result.steps}"
            if after_clean:
                after_clean(ctl, f"completed after {k} changes")
            assert k >= min_points, f"the matrix only covered {k} crash points: {result.ops}"
            return k
        context = f"killed before change {k}, {result.died_before}"
        if recovering:
            recover(w, ctl, context)
        after_crash(ctl, context)
        k += 1
        assert k < MAX_CRASH_POINTS, "the matrix does not end"


def victim(*steps: dict) -> list[dict]:
    """Open unarmed, run the operation armed, close armed: release is part of every operation."""
    first, *rest = steps
    return [{"do": "open"}, {**first, "arm": True}, *rest, {"do": "close"}]


def again(w: World, ctl: Path, *steps: dict) -> Result:
    result = Actor(ctl, "retry", w.root, [{"do": "open"}, *steps, {"do": "close"}]).run()
    assert not result.unexpected(), result.unexpected()
    return result


# ---------------------------------------------------------------------------------------- journal commit
def test_commit_is_all_or_nothing_and_retryable(tmp_path):
    w = build(tmp_path)
    rid = "root_" + "c" * 32
    steps = victim({"do": "commit", "id": rid, "name": "crashy", "expect_rev": 0, "txid": TXID})

    def after_crash(ctl, context):
        with Workspace.open(w.root) as ws:
            outcome = ws.transaction_outcome(TXID)
            assert (outcome["state"] == "committed") == (ws.get("root", rid) is not None), context
            assert outcome["state"] in ("committed", "not-found"), context
        retried = again(w, ctl, {"do": "commit", "id": rid, "name": "crashy", "expect_rev": 0, "txid": TXID})
        assert retried.clean, f"{context}: {retried.steps}"
        with Workspace.open(w.root) as ws:
            entries = [e for e in ws.entries_after(0) if e["txid"] == TXID]
            assert len(entries) == 1, f"{context}: retried into a duplicate"
            assert retried.step("commit")["value"] == entries[0]["seq"], context
        assert_consistent(w, context)
    assert crash_matrix(w, steps, after_crash, min_points=4)


# ---------------------------------------------------------------------------------------- check-in
def test_checkin_is_all_or_nothing_and_a_retry_never_duplicates(tmp_path):
    edited = b"IPT v2 edited"
    w = build(tmp_path, checked_out=True, edited=edited)
    args = {"do": "checkin", "doc": w.doc, "token": w.token, "txid": TXID}
    steps = victim(args)

    def after_crash(ctl, context):
        with Workspace.open(w.root) as ws:
            state = ws.transaction_outcome(TXID)["state"]
            doc, co = ws.get("document", w.doc), ws.get("checkout", w.doc)
            if state == "committed":
                assert doc["rev_count"] == 2 and co is None, context
            else:
                assert state == "not-found", context
                assert doc["rev_count"] == 1 and co["token"] == w.token, f"{context}: half a check-in"
        assert w.path.read_bytes() == edited, f"{context}: the working file changed"
        retried = again(w, ctl, args)
        assert retried.clean, f"{context}: retry refused: {retried.steps}"
        with Workspace.open(w.root) as ws:
            chain = D.revisions_of(ws, w.doc)
            assert [r["id"] for r in chain][-1] == retried.step("checkin")["value"], context
            assert len(chain) == 2 and ws.get("checkout", w.doc) is None, f"{context}: retry duplicated the revision"
            assert chain[-1]["sha256"] == hashlib.sha256(edited).hexdigest(), context
        assert_consistent(w, context)
    assert crash_matrix(w, steps, after_crash, min_points=6)


# ---------------------------------------------------------------------------------------- register
def test_register_leaves_one_document_or_none(tmp_path):
    def prepare(w, ws):
        w.new_file = w.path.with_name("new part.ipt")
        w.new_file.write_bytes(b"NEW")
    w = build(tmp_path, prepare=prepare)
    args = {"do": "register", "project": w.project, "file": str(w.new_file)}

    def after_crash(ctl, context):
        def registered():
            with Workspace.open(w.root) as ws:
                return [d for d in ws.list("document").values() if d["rel_path"].endswith("new part.ipt")]
        had = registered()
        retried = again(w, ctl, args)
        assert retried.step("register")["ok"] != bool(had), f"{context}: {retried.steps}"
        now = registered()
        assert len(now) == 1 and now[0]["rev_count"] == 1, f"{context}: {now}"
        assert w.new_file.read_bytes() == b"NEW", f"{context}: registration changed the file"
        assert_consistent(w, context)
    assert crash_matrix(w, victim(args), after_crash, min_points=6)


# ---------------------------------------------------------------------------------------- checkout
def test_checkout_exists_or_does_not_and_touches_no_file(tmp_path):
    w = build(tmp_path)

    def after_crash(ctl, context):
        assert w.path.read_bytes() == b"IPT v1", context
        with Workspace.open(w.root) as ws:
            held = ws.get("checkout", w.doc)
        retried = again(w, ctl, {"do": "checkout", "doc": w.doc})
        if held:
            assert retried.step("checkout")["error"] == "CheckoutConflict", f"{context}: {retried.steps}"
        else:
            assert retried.step("checkout")["ok"], f"{context}: {retried.steps}"
        assert_consistent(w, context)
    assert crash_matrix(w, victim({"do": "checkout", "doc": w.doc}), after_crash, min_points=4)


# ---------------------------------------------------------------------------------------- cancel with revert
def _rwx_temps(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir() if p.name != path.name)


def test_cancel_with_revert_never_loses_the_file_and_finishes_on_retry(tmp_path):
    edits = b"unsaved engineering work"
    w = build(tmp_path, checked_out=True, edited=edits)
    args = {"do": "cancel", "doc": w.doc, "token": w.token, "revert": True}

    def after_crash(ctl, context):
        body = w.path.read_bytes()
        assert body in (edits, b"IPT v1"), f"{context}: working file is {body!r}"
        with Workspace.open(w.root) as ws:
            held = ws.get("checkout", w.doc) is not None
        assert held or body == b"IPT v1", f"{context}: checkout released but the file was not reverted"
        leftovers = _rwx_temps(w.path)
        assert all(".rwx-" in n and n.endswith(".tmp") for n in leftovers), f"{context}: {leftovers}"
        if held:
            retried = again(w, ctl, args)
            assert retried.clean, f"{context}: {retried.steps}"
            assert w.path.read_bytes() == b"IPT v1", context
        assert_consistent(w, context)
    assert crash_matrix(w, victim(args), after_crash, min_points=4)


# ---------------------------------------------------------------------------------------- restore to working
def test_restore_never_leaves_a_partial_working_file(tmp_path):
    edits = b"later edits"

    def prepare(w, ws):
        w.old_rev = w.rev
        w.token = D.checkout(ws, w.doc)["token"]
        w.path.write_bytes(b"IPT v2")
        D.checkin(ws, w.doc, w.token, "second")
        w.token = D.checkout(ws, w.doc)["token"]
        w.path.write_bytes(edits)
    w = build(tmp_path, prepare=prepare)
    args = {"do": "restore", "doc": w.doc, "token": "", "rev": w.old_rev}
    args["token"] = w.token

    def after_crash(ctl, context):
        body = w.path.read_bytes()
        assert body in (edits, b"IPT v1"), f"{context}: working file is {body!r}"
        with Workspace.open(w.root) as ws:
            co = ws.get("checkout", w.doc)
        assert co is not None and co["token"] == w.token, context
        assert co["restored_from"] is None or body == b"IPT v1", f"{context}: claims a restore that did not happen"
        retried = again(w, ctl, args)
        assert retried.clean, f"{context}: {retried.steps}"
        assert w.path.read_bytes() == b"IPT v1", context
        assert_consistent(w, context)
    assert crash_matrix(w, victim(args), after_crash, min_points=4)


# ---------------------------------------------------------------------------------------- lifecycle
def test_release_and_supersede_happen_together(tmp_path):
    def prepare(w, ws):
        lc = dict(D.get_lifecycle(ws))
        D.save_lifecycle(ws, {**lc, "require_distinct_approver": False})
        D.transition(ws, w.rev, "in_review")
        D.transition(ws, w.rev, "approved")
        D.transition(ws, w.rev, "released")
        token = D.checkout(ws, w.doc)["token"]
        w.path.write_bytes(b"IPT v2")
        w.second = D.checkin(ws, w.doc, token, "second")["id"]
        D.transition(ws, w.second, "in_review")
        D.transition(ws, w.second, "approved")
    w = build(tmp_path, prepare=prepare)

    def after_crash(ctl, context):
        with Workspace.open(w.root) as ws:
            doc, first, second = ws.get("document", w.doc), ws.get("revision", w.rev), ws.get("revision", w.second)
        pair = (first["state"], second["state"], doc["released_rev_id"])
        assert pair in (("released", "approved", w.rev), ("superseded", "released", w.second)), f"{context}: {pair}"
        assert_consistent(w, context)
    assert crash_matrix(w, victim({"do": "transition", "rev": w.second, "to": "released"}), after_crash, min_points=4)


# ---------------------------------------------------------------------------------------- projects
def test_create_project_registers_a_complete_folder_or_nothing(tmp_path):
    w = build(tmp_path)
    args = {"do": "create_project", "name": "New job", "number": "J-2"}

    def after_crash(ctl, context):
        with Workspace.open(w.root) as ws:
            made = [p for p in ws.list("project").values() if p["name"] == "New job"]
            if made:
                assert (projects.project_folder(ws, made[0]) / "02 CAD").is_dir(), f"{context}: registered, no folder"
        retried = again(w, ctl, args)
        step = retried.step("create_project")
        assert not step.get("unexpected"), f"{context}: {step}"
        if made:
            assert step["error"] == "ProjectError", f"{context}: created twice? {step}"
        assert_consistent(w, context)
    assert crash_matrix(w, victim(args), after_crash, min_points=4)


def test_adopt_registers_or_not_and_never_touches_the_folder(tmp_path):
    def prepare(w, ws):
        w.old_job = w.root / "Engineering" / "Old job"
        (w.old_job / "sub").mkdir(parents=True)
        (w.old_job / "a.txt").write_bytes(b"a")
        (w.old_job / "sub" / "b.ipt").write_bytes(b"b")
    w = build(tmp_path, prepare=prepare)
    args = {"do": "adopt", "folder": str(w.root / "Engineering" / "Old job"), "name": "Old job"}

    def tree():
        return {str(p.relative_to(w.old_job)): (p.read_bytes() if p.is_file() else None) for p in w.old_job.rglob("*")}
    before = {"a.txt": b"a", "sub": None, str(Path("sub") / "b.ipt"): b"b"}

    def after_crash(ctl, context):
        assert tree() == before, f"{context}: adoption changed the folder"
        with Workspace.open(w.root) as ws:
            had = [p for p in ws.list("project").values() if p["name"] == "Old job"]
        retried = again(w, ctl, args)
        assert retried.step("adopt")["ok"] != bool(had), f"{context}: {retried.steps}"
        with Workspace.open(w.root) as ws:
            now = [p for p in ws.list("project").values() if p["name"] == "Old job"]
            assert len(now) == 1 and now[0]["baseline"]["files"] == 2, context
            assert ws.read_blob(now[0]["baseline"]["blob"])["files"], context
        assert_consistent(w, context)
    assert crash_matrix(w, victim(args), after_crash, min_points=3)


# ---------------------------------------------------------------------------------------- content GC
def test_gc_only_ever_removes_unreferenced_content(tmp_path):
    def prepare(w, ws):
        store = ContentStore(ws)
        for i in range(3):
            orphan = w.path.with_name(f"orphan{i}.bin")
            orphan.write_bytes(b"orphan %d" % i)
            w.orphans = getattr(w, "orphans", []) + [store.store_file(orphan).sha256]
            orphan.unlink()
        w.store_root = store.root
    w = build(tmp_path, prepare=prepare)
    steps = victim({"do": "gc", "apply": True, "min_age": 0.0})

    def after_crash(ctl, context):
        retried = again(w, ctl, {"do": "gc", "apply": True, "min_age": 0.0})
        assert retried.clean, f"{context}: {retried.steps}"
        with Workspace.open(w.root) as ws:
            store = ContentStore(ws)
            assert not any(store.exists(sha) for sha in w.orphans), f"{context}: orphans survived a clean retry"
        assert_consistent(w, context)
    assert crash_matrix(w, steps, after_crash, min_points=4)


# ---------------------------------------------------------------------------------------- verify --repair
def test_repair_preserves_what_it_replaces_and_reads_stay_right(tmp_path):
    rogue = "root_" + "f" * 32

    def prepare(w, ws):
        path = ws.meta_dir / "records" / "document" / f"{w.doc}.json"
        damaged = atomicio.read_json(path)
        damaged["title"] = "tampered"
        atomicio.atomic_write_json(path, damaged)
        atomicio.atomic_write_json(ws.meta_dir / "records" / "root" / f"{rogue}.json", {"name": "rogue", REV_FIELD: 1})
    w = build(tmp_path, prepare=prepare)

    def after_crash(ctl, context):
        with Workspace.open(w.root) as ws:
            assert ws.get("root", rogue) is None, f"{context}: read a record the journal never wrote"
        retried = again(w, ctl, {"do": "repair"})
        assert retried.clean, f"{context}: {retried.steps}"
        with Workspace.open(w.root) as ws:
            assert ws.get("document", w.doc)["title"] == "bracket", f"{context}: repair did not restore the record"
        assert_consistent(w, context)
        kept = [p for p in (w.meta / "quarantine").rglob("*.json")] if (w.meta / "quarantine").exists() else []
        names = [p.name for p in kept]
        assert any(rogue in n for n in names), f"{context}: the unexplained file was discarded: {names}"
    assert crash_matrix(w, victim({"do": "repair"}), after_crash, min_points=4)


# ---------------------------------------------------------------------------------------- locks
def _tokens_accounted_for(w: World, token: str, context: str) -> None:
    """The heart of ADR-019: a lock file stops blocking only after its token is revoked."""
    locks = w.meta / "locks"
    blocking = [locks / "writer.lock", *locks.glob(".releasing-*"), *locks.glob(".fencing-*"), *locks.glob(".clearing-*")]
    present = False
    for path in blocking:
        try:
            present = present or atomicio.read_json(path).get("token") == token
        except (OSError, ValueError):
            pass
    assert present or token in Workspace._revoked_tokens(w.meta), f"{context}: live token {token} is unaccounted for"


def test_release_crash_at_every_step(tmp_path):
    w = build(tmp_path)

    def after_crash(ctl, context):
        assert_consistent(w, context)
    assert crash_matrix(w, [{"do": "open"}, {"do": "close", "arm": True}], after_crash, min_points=1)


def test_clearing_a_revoked_lock_crash_at_every_step(tmp_path):
    def prepare(w, ws):
        pass
    w = build(tmp_path, prepare=prepare)
    holder = Workspace.open(w.root, writable=True)
    Workspace._publish_revocation(w.meta, atomicio.read_json(w.meta / "locks" / "writer.lock"), reason="t", evidence="x")
    holder._lock_token = None
    holder.close()
    shutil.rmtree(w.base / "snapshot")
    w.snapshot()
    steps = [{"do": "open", "arm": True}, {"do": "commit", "id": "root_" + "d" * 32, "expect_rev": 0}, {"do": "close"}]

    def after_crash(ctl, context):
        assert_consistent(w, context)
    assert crash_matrix(w, steps, after_crash, min_points=4)


def test_fencing_an_orphan_crash_at_every_step(tmp_path):
    token = "b" * 32
    w = build(tmp_path)
    atomicio.atomic_write_json(w.meta / "locks" / ".releasing-feed", {"token": token, "user": "u", "host": "h", "pid": 1})
    shutil.rmtree(w.base / "snapshot")
    w.snapshot()
    steps = [{"do": "open", "arm": True}, {"do": "close"}]

    def before_recovery(ctl, context):
        _tokens_accounted_for(w, token, context)
        recover(w, ctl, context)
        assert token in Workspace._revoked_tokens(w.meta), f"{context}: orphan's writer was not revoked"
        assert_consistent(w, context)
    assert crash_matrix(w, steps, before_recovery, recovering=False, min_points=4)


def test_break_crash_at_every_step_never_loses_the_token(tmp_path):
    w = build(tmp_path)
    dead = Actor(tmp_path / "ctl-dead", "dead", w.root, [{"do": "open"}, {"do": "sleep_forever"}],
                 pauses=[]).start()
    dead.wait_at("forever")
    token = Workspace.read_writer_lock(w.root)["token"]
    dead.kill()
    shutil.rmtree(w.base / "snapshot")
    w.snapshot()
    steps = [{"do": "break_lock", "token": token, "arm": True}]

    def before_recovery(ctl, context):
        _tokens_accounted_for(w, token, context)
        recover(w, ctl, context)
        assert_consistent(w, context)
    assert crash_matrix(w, steps, before_recovery, recovering=False, min_points=3)


def test_clear_orphan_crash_at_every_step_resumes_exactly_once(tmp_path):
    w = build(tmp_path)
    orphan = w.meta / "locks" / ".releasing-beef"
    orphan.write_bytes(b"unreadable bytes")
    shutil.rmtree(w.base / "snapshot")
    w.snapshot()
    steps = [{"do": "clear_orphan", "name": orphan.name, "arm": True}]

    def after_crash(ctl, context):
        with Workspace.open(w.root) as ws:
            authorised = any(e["op"] == Workspace.ORPHAN_AUTH_OP for e in ws.entries_after(0))
        if not authorised:
            result = Actor(ctl, "writer", w.root, [{"do": "open"}]).run()
            assert result.step("open")["error"] == "WorkspaceCorruptError", f"{context}: writer got in before an audit"
        retried = Actor(ctl, "retry", w.root, [{"do": "clear_orphan", "name": orphan.name}]).run()
        assert retried.clean and retried.step("clear_orphan")["value"] == "completed", f"{context}: {retried.steps}"
        recover(w, ctl, context)
        with Workspace.open(w.root) as ws:
            results = [e for e in ws.entries_after(0) if e["op"] == Workspace.ORPHAN_RESULT_OP]
            assert len(results) == 1, f"{context}: {len(results)} terminal results"
        evidence = list((w.meta / "locks").glob("writer.lock.orphaned-*"))
        assert [p.read_bytes() for p in evidence] == [b"unreadable bytes"], f"{context}: {evidence}"
        assert_consistent(w, context)
    assert crash_matrix(w, steps, after_crash, recovering=False, min_points=4)


# ---------------------------------------------------------------------------------------- create
def test_create_workspace_crash_at_every_step_can_be_finished(tmp_path):
    root = tmp_path / "fresh"
    w = World(base=tmp_path, root=root)
    steps = [{"do": "create_workspace", "name": "fresh", "arm": True}, {"do": "close"}]

    def after_crash(ctl, context):
        if not (w.meta / "workspace.json").exists():
            finished = Actor(ctl, "again", w.root, [{"do": "create_workspace"}, {"do": "close"}]).run()
            assert finished.clean, f"{context}: cannot create again: {finished.steps}"
        else:
            holder = Workspace.read_writer_lock(w.root)
            opener = [{"do": "open"}, {"do": "close"}]
            if holder:
                opener.insert(0, {"do": "break_lock", "token": holder["token"]})
            opened = Actor(ctl, "again", w.root, opener).run()
            assert opened.clean, f"{context}: cannot open: {opened.steps}"
        with Workspace.open(w.root) as ws:
            assert ws.verify()["problems"] == [], context
            assert ws.settings()["new_project_mode"], context
        assert_consistent(w, context)
    assert crash_matrix(w, steps, after_crash, recovering=False, min_points=6)


# ---------------------------------------------------------------------------------------- the harness itself
def test_the_victim_really_dies_without_cleanup(tmp_path):
    """Controls for the harness: a kill must skip finally and atexit, or these results prove less than claimed."""
    w = build(tmp_path)
    ctl = w.reset()
    result = Actor(ctl, "victim", w.root, victim({"do": "commit", "id": "root_" + "c" * 32, "expect_rev": 0}),
                   die_at=0).run()
    assert result.killed and not result.steps and result.died_before
    assert Workspace.read_writer_lock(w.root) is not None, "a killed process cannot have released its lock"
    assert result.exit_code != 0 and os.path.exists(w.meta / "locks" / "writer.lock")
    with Workspace.open(w.root) as ws:
        assert ws.head_seq() == ws.applied_seq()
    probe = RecordPut("root", "root_" + "c" * 32, {"name": "x"})
    assert probe.as_json()["id"].endswith("c" * 32)


@pytest.mark.parametrize("op", ["replace", "link", "unlink", "mkdir", "create"])
def test_harness_counts_each_kind_of_change(tmp_path, op):
    """If a kind of filesystem change stopped being counted, crash points would silently disappear."""
    w = build(tmp_path)
    ctl = w.reset()
    steps = victim({"do": "register", "project": w.project, "file": str(w.path)}, ) if op != "create" else None
    edited = w.path.with_name("counted.ipt")
    edited.write_bytes(b"counted")
    steps = victim({"do": "register", "project": w.project, "file": str(edited)})
    result = Actor(ctl, "victim", w.root, steps).run()
    assert result.clean, result.steps
    wanted = {"link": ("link", "rename"), "replace": ("replace",), "unlink": ("unlink", "rename"),
              "mkdir": ("mkdir", "rename", "link", "replace"), "create": ("create",)}[op]
    assert any(o.startswith(wanted) for o in result.ops), (op, result.ops)
