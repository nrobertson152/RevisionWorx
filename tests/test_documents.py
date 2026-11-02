# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 2: document registry, revisions, lifecycle, checkout/check-in (ADR-010..013)."""
import hashlib
import multiprocessing as mp
import os
import time

import pytest

import proc_helpers
from conftest import tree_snapshot
from revisionworx import atomicio, documents as D, projects, revlabels
from revisionworx.content import ContentStore, SourceChangedError
from revisionworx.lifecycle import DEFAULT_LIFECYCLE, LifecycleError, validate_lifecycle
from revisionworx.projects import NewProject
from revisionworx.workspace import CommittedNotApplied, ConflictError, RecordPut, Workspace


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def proj(ws, share):
    rec = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
    folder = projects.project_folder(ws, rec)
    cad = folder / "02 CAD" / "Parts"
    (cad / "bracket.ipt").write_bytes(b"IPT v1")
    (folder / "02 CAD" / "Assemblies" / "frame.iam").write_bytes(b"IAM refs ../Parts/bracket.ipt")
    (folder / "03 Drawings" / "Native" / "bracket.idw").write_bytes(b"IDW v1")
    ws.folder = folder
    return rec


def reg(ws, proj, rel, **kw):
    return D.register_document(ws, proj["id"], ws.folder / rel, **kw)


# ---------------------------------------------------------------- labels and configuration
def test_alpha_labels_skip_asme_letters():
    labels = [revlabels.alpha_label(n) for n in range(1, 23)]
    assert labels[:8] == ["A", "B", "C", "D", "E", "F", "G", "H"]
    assert not set("IOQSXZ") & set("".join(labels))
    assert labels[19] == "Y" and labels[20] == "AA" and labels[21] == "AB"
    assert revlabels.label_for("numeric2", 3) == "03" and revlabels.label_for("manual", 1) is None
    with pytest.raises(ValueError):
        revlabels.validate_label("bad label!")


def test_default_lifecycle_validates_and_bad_ones_are_rejected():
    assert validate_lifecycle(DEFAULT_LIFECYCLE)["initial"] == "wip"
    bad = dict(DEFAULT_LIFECYCLE, transitions=DEFAULT_LIFECYCLE["transitions"] + [["released", "superseded"]])
    with pytest.raises(LifecycleError):
        validate_lifecycle(bad)
    with pytest.raises(LifecycleError):
        validate_lifecycle(dict(DEFAULT_LIFECYCLE, released="nope"))


def test_save_lifecycle_cannot_drop_states_in_use(ws, proj):
    doc, rev = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    lc = dict(DEFAULT_LIFECYCLE)
    lc["states"] = [s for s in lc["states"] if s["id"] != "wip"] + [{"id": "draft", "name": "Draft"}]
    lc["initial"] = "draft"
    lc["transitions"] = [["draft", "in_review"], ["in_review", "approved"], ["approved", "released"]]
    with pytest.raises(LifecycleError, match="wip"):
        D.save_lifecycle(ws, lc)


def test_custom_doctype_and_suggestion(ws):
    D.save_doctype(ws, {"id": "hyd-calc", "name": "Hydraulic calculation", "extensions": ["xlsx"],
                        "revision_scheme": "numeric2"})
    assert D.suggest_type(ws, "pump sizing.XLSX") == "hyd-calc"
    assert D.suggest_type(ws, "a.slddrw") == "drawing"
    assert D.suggest_type(ws, "notes.txt") == "general"
    with pytest.raises(D.DocumentError):
        D.save_doctype(ws, {"id": "Bad Id"})


# ---------------------------------------------------------------- registry
def test_register_captures_first_revision_without_touching_files(ws, proj):
    before = tree_snapshot(ws.folder)
    doc, rev = reg(ws, proj, "02 CAD/Parts/bracket.ipt", number="P-100", title="Bracket")
    assert tree_snapshot(ws.folder) == before  # not moved, renamed or rewritten
    assert doc["rel_path"] == "02 CAD/Parts/bracket.ipt" and doc["type_id"] == "cad-part"
    assert rev["label"] == "A" and rev["state"] == "wip" and rev["sha256"] == sha(b"IPT v1")
    store = ContentStore(ws)
    assert store.path_for(rev["sha256"]).read_bytes() == b"IPT v1" and store.verify(rev["sha256"])
    assert ws.get("document", doc["id"])["latest_rev_id"] == rev["id"]
    assert ws.verify()["problems"] == []


def test_register_without_capture(ws, proj):
    doc, rev = reg(ws, proj, "03 Drawings/Native/bracket.idw", capture=False)
    assert rev is None and doc["rev_count"] == 0 and doc["type_id"] == "drawing"
    assert D.document_status(ws, doc["id"])["state"] == "unrevisioned"


def test_register_refusals(ws, proj, share, tmp_path):
    reg(ws, proj, "02 CAD/Parts/bracket.ipt", number="P-1")
    variant = ws.folder / "02 CAD/Parts/BRACKET.ipt"
    variant.write_bytes(b"case variant")  # a distinct file on Linux, the same name on Windows
    with pytest.raises(D.DocumentError, match="already registered"):
        D.register_document(ws, proj["id"], variant)
    with pytest.raises(D.DocumentError, match="already used"):
        reg(ws, proj, "03 Drawings/Native/bracket.idw", number="p-1")
    with pytest.raises(D.DocumentError, match="does not exist"):
        reg(ws, proj, "02 CAD/Parts/missing.ipt")
    outside = tmp_path / "outside.ipt"
    outside.write_bytes(b"x")
    with pytest.raises(Exception):
        D.register_document(ws, proj["id"], outside)
    if hasattr(os, "symlink"):
        try:
            os.symlink(ws.folder / "03 Drawings/Native/bracket.idw", ws.folder / "02 CAD/Parts/link.ipt")
        except OSError:
            pass
        else:
            with pytest.raises(D.DocumentError, match="not a regular file"):
                reg(ws, proj, "02 CAD/Parts/link.ipt")
    legacy = share / "Engineering" / "Legacy"
    legacy.mkdir()
    (legacy / "a.ipt").write_bytes(b"a")
    indexed = projects.adopt_project(ws, legacy, name="Legacy")
    with pytest.raises(D.DocumentError, match="managed"):
        D.register_document(ws, indexed["id"], legacy / "a.ipt")


def test_manual_scheme_requires_label(ws, proj):
    with pytest.raises(D.DocumentError, match="manual"):
        reg(ws, proj, "02 CAD/Parts/bracket.ipt", revision_scheme="manual")
    doc, rev = reg(ws, proj, "02 CAD/Parts/bracket.ipt", revision_scheme="manual", label="P1")
    assert rev["label"] == "P1"


# ---------------------------------------------------------------- checkout / check-in
def test_checkout_is_exclusive(ws, proj):
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    co = D.checkout(ws, doc["id"])
    with pytest.raises(D.CheckoutConflict, match="already checked out"):
        D.checkout(ws, doc["id"])
    assert ws.get("checkout", doc["id"])["token"] == co["token"]


def test_checkout_race_between_processes_has_one_winner(ws, proj, share):
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    ws.close()
    ctx = mp.get_context("spawn")
    n = 6
    barrier, results = ctx.Barrier(n), ctx.Queue()
    procs = [ctx.Process(target=proc_helpers.race_checkout, args=(str(share), doc["id"], barrier, results))
             for _ in range(n)]
    for p in procs:
        p.start()
    outcomes = [results.get(timeout=120) for _ in range(n)]
    for p in procs:
        p.join(60)
    assert outcomes.count("won") == 1, outcomes
    assert outcomes.count("conflict") == n - 1, outcomes
    with Workspace.open(share) as ro:
        assert ro.verify()["problems"] == []


def test_checkin_creates_immutable_revision_and_releases_checkout(ws, proj):
    doc, rev1 = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    others_before = {k: v for k, v in tree_snapshot(ws.folder).items() if "bracket.ipt" not in k}
    co = D.checkout(ws, doc["id"])
    (ws.folder / "02 CAD/Parts/bracket.ipt").write_bytes(b"IPT v2 thicker")
    assert D.document_status(ws, doc["id"])["state"] == "modified"
    rev2 = D.checkin(ws, doc["id"], co["token"], "Increase thickness to 6 mm")
    assert rev2["label"] == "B" and rev2["previous_rev_id"] == rev1["id"] and rev2["state"] == "wip"
    assert rev2["sha256"] == sha(b"IPT v2 thicker")
    assert ws.get("checkout", doc["id"]) is None
    assert ws.get("revision", rev1["id"])["sha256"] == sha(b"IPT v1")  # history intact
    after = {k: v for k, v in tree_snapshot(ws.folder).items() if "bracket.ipt" not in k}
    for k, v in others_before.items():  # assembly referencing the part is untouched
        if v[0] == "file":
            assert after[k] == v
    assert D.document_status(ws, doc["id"])["state"] == "unchanged"
    assert [r["label"] for r in D.history(ws, doc["id"])] == ["A", "B"]


def test_checkin_validations(ws, proj):
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    with pytest.raises(D.NotCheckedOut):
        D.checkin(ws, doc["id"], "x" * 32, "desc")
    co = D.checkout(ws, doc["id"])
    with pytest.raises(D.TokenMismatch):
        D.checkin(ws, doc["id"], "wrong", "desc")
    with pytest.raises(D.DocumentError, match="description"):
        D.checkin(ws, doc["id"], co["token"], "  ")
    with pytest.raises(D.NoChanges):
        D.checkin(ws, doc["id"], co["token"], "nothing changed")
    assert ws.get("checkout", doc["id"]) is not None  # failed check-in keeps the checkout
    rev = D.checkin(ws, doc["id"], co["token"], "re-issue unchanged", allow_unchanged=True)
    assert rev["label"] == "B"


def test_checkin_aborts_when_file_changes_during_copy(ws, proj, monkeypatch):
    import revisionworx.content as content_mod
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    co = D.checkout(ws, doc["id"])
    path = ws.folder / "02 CAD/Parts/bracket.ipt"
    path.write_bytes(b"IPT being saved by CAD")
    real_stat, calls = os.stat, []

    def stat_changes(p, *a, **kw):
        st = real_stat(p, *a, **kw)
        if str(p) == str(path):
            calls.append(1)
            if len(calls) == 2:  # second look: CAD rewrote the file meanwhile
                os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
                return real_stat(p, *a, **kw)
        return st
    monkeypatch.setattr(content_mod.os, "stat", stat_changes)
    head = ws.head_seq()
    with pytest.raises(SourceChangedError):
        D.checkin(ws, doc["id"], co["token"], "v2")
    monkeypatch.undo()
    assert ws.head_seq() == head and ws.get("checkout", doc["id"]) is not None


def test_crash_before_commit_leaves_no_revision_and_retry_works(ws, proj, monkeypatch):
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    co = D.checkout(ws, doc["id"])
    (ws.folder / "02 CAD/Parts/bracket.ipt").write_bytes(b"IPT v2")
    real = atomicio.publish_exclusive

    def crash(path, data):
        if "journal" in str(path):
            raise OSError("network share disconnected")
        return real(path, data)
    monkeypatch.setattr(atomicio, "publish_exclusive", crash)
    with pytest.raises(OSError):
        D.checkin(ws, doc["id"], co["token"], "v2")
    monkeypatch.undo()
    assert ws.get("document", doc["id"])["rev_count"] == 1
    assert ws.get("checkout", doc["id"]) is not None
    assert ContentStore(ws).exists(sha(b"IPT v2"))  # orphan object is harmless
    rev = D.checkin(ws, doc["id"], co["token"], "v2")  # retry reuses (re-verifies) the object
    assert rev["sha256"] == sha(b"IPT v2") and ws.verify()["problems"] == []


def test_failure_after_commit_point_is_reported_as_committed(ws, proj, share, monkeypatch):
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    co = D.checkout(ws, doc["id"])
    (ws.folder / "02 CAD/Parts/bracket.ipt").write_bytes(b"IPT v2")

    def boom(entry):
        raise OSError("disk full while materialising")
    monkeypatch.setattr(ws, "_apply", boom)
    with pytest.raises(CommittedNotApplied):
        D.checkin(ws, doc["id"], co["token"], "v2", txid="c" * 32)
    monkeypatch.undo()
    assert ws.transaction_outcome("c" * 32)["state"] == "committed"
    token = Workspace.read_writer_lock(share)["token"]
    ws._lock_token = None
    ws.close()
    Workspace.break_writer_lock(share, token, reason="test")
    with Workspace.open(share, writable=True) as w2:
        d = w2.get("document", doc["id"])
        assert d["rev_count"] == 2 and w2.get("checkout", doc["id"]) is None
        assert w2.verify()["problems"] == []


def test_admin_release_and_stale_checkout(ws, proj):
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    lost = D.checkout(ws, doc["id"])  # this client's token is "lost"
    with pytest.raises(D.DocumentError):
        D.admin_release_checkout(ws, doc["id"], lost["token"], reason=" ")
    with pytest.raises(D.DocumentError, match="changed"):
        D.admin_release_checkout(ws, doc["id"], "other", reason="laptop died")
    D.admin_release_checkout(ws, doc["id"], lost["token"], reason="laptop died")
    audit = [a for a in ws.list("audit").values() if a["event"] == "checkout-admin-release"]
    assert audit and audit[0]["previous_checkout"]["token"] == lost["token"]
    co2 = D.checkout(ws, doc["id"])
    (ws.folder / "02 CAD/Parts/bracket.ipt").write_bytes(b"IPT by Bob")
    D.checkin(ws, doc["id"], co2["token"], "Bob's change")
    with pytest.raises(D.NotCheckedOut):  # the stale token cannot sneak a revision in
        D.checkin(ws, doc["id"], lost["token"], "Alice's late change")


def test_stale_checkout_base_is_detected(ws, proj):
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    co = D.checkout(ws, doc["id"])
    # Simulate an inconsistent authoritative state: a revision appeared after the checkout's base.
    d = ws.get("document", doc["id"])
    ws.commit("t", [RecordPut("document", doc["id"], {**d, "latest_rev_id": "rev_" + "f" * 32})])
    with pytest.raises(D.StaleCheckout):
        D.checkin(ws, doc["id"], co["token"], "x", allow_unchanged=True)


def test_cancel_checkout_with_and_without_revert(ws, proj):
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    path = ws.folder / "02 CAD/Parts/bracket.ipt"
    co = D.checkout(ws, doc["id"])
    path.write_bytes(b"experimental edit")
    D.cancel_checkout(ws, doc["id"], co["token"])
    assert path.read_bytes() == b"experimental edit"  # never discarded without being asked
    assert D.document_status(ws, doc["id"])["state"] == "modified-externally"
    co = D.checkout(ws, doc["id"])
    D.cancel_checkout(ws, doc["id"], co["token"], revert=True)
    assert path.read_bytes() == b"IPT v1" and ws.get("checkout", doc["id"]) is None


def test_external_modification_detection(ws, proj):
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    path = ws.folder / "02 CAD/Parts/bracket.ipt"
    assert D.document_status(ws, doc["id"])["state"] == "unchanged"
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))  # touched, same bytes
    s = D.document_status(ws, doc["id"])
    assert s["state"] == "unchanged" and s["method"] == "hash"
    path.write_bytes(b"IPT v9")  # edited outside RevisionWorx, same length trick not needed
    assert D.document_status(ws, doc["id"])["state"] == "modified-externally"
    assert D.document_status(ws, doc["id"], verify_hash=True)["state"] == "modified-externally"
    path.unlink()
    assert D.document_status(ws, doc["id"])["state"] == "missing"


# ---------------------------------------------------------------- lifecycle
def _new_rev(ws, doc, content, desc):
    co = D.checkout(ws, doc["id"])
    working = D.working_path(ws, ws.get("document", doc["id"]))
    working.write_bytes(content)
    return D.checkin(ws, doc["id"], co["token"], desc)


def test_lifecycle_release_supersedes_previous(ws, proj):
    doc, a = reg(ws, proj, "03 Drawings/Native/bracket.idw", number="DWG-1")
    with pytest.raises(LifecycleError, match="allowed: in_review, obsolete"):
        D.transition(ws, a["id"], "released")
    for state in ("in_review", "approved", "released"):
        a = D.transition(ws, a["id"], state, comment=f"to {state}")
    assert ws.get("document", doc["id"])["released_rev_id"] == a["id"]
    b = _new_rev(ws, doc, b"IDW v2", "Add tolerance note")
    for state in ("in_review", "approved", "released"):
        b = D.transition(ws, b["id"], state)
    a_now = ws.get("revision", a["id"])
    assert a_now["state"] == "superseded" and a_now["transitions"][-1]["system"] is True
    assert ws.get("document", doc["id"])["released_rev_id"] == b["id"]
    assert [t["to"] for t in ws.get("revision", b["id"])["transitions"]] == ["in_review", "approved", "released"]
    with pytest.raises(LifecycleError):
        D.transition(ws, a["id"], "released")  # superseded cannot be re-released
    D.transition(ws, b["id"], "obsolete", comment="product discontinued")
    assert ws.get("document", doc["id"])["released_rev_id"] is None


def test_cannot_release_older_than_current_release(ws, proj):
    doc, a = reg(ws, proj, "03 Drawings/Native/bracket.idw")
    b = _new_rev(ws, doc, b"IDW v2", "v2")
    for r in (b, a):
        for state in ("in_review", "approved"):
            D.transition(ws, r["id"], state)
    D.transition(ws, b["id"], "released")
    with pytest.raises(LifecycleError, match="newer"):
        D.transition(ws, a["id"], "released")


def test_distinct_approver_policy(ws, proj):
    lc = dict(DEFAULT_LIFECYCLE, require_distinct_approver=True)
    D.save_lifecycle(ws, lc)
    doc, a = reg(ws, proj, "03 Drawings/Native/bracket.idw")
    D.transition(ws, a["id"], "in_review")
    with pytest.raises(LifecycleError, match="different person"):
        D.transition(ws, a["id"], "approved")


def test_release_refuses_damaged_content(ws, proj):
    doc, a = reg(ws, proj, "03 Drawings/Native/bracket.idw")
    for state in ("in_review", "approved"):
        D.transition(ws, a["id"], state)
    obj = ContentStore(ws).path_for(a["sha256"])
    os.chmod(obj, 0o644)
    obj.write_bytes(b"bit rot")
    with pytest.raises(Exception, match="damaged"):
        D.transition(ws, a["id"], "released")
    assert D.verify_revisions(ws)[0]["revision_id"] == a["id"]


def test_released_revision_fields_are_immutable(ws, proj):
    doc, a = reg(ws, proj, "03 Drawings/Native/bracket.idw")
    tampered = {**a, "sha256": "0" * 64}
    with pytest.raises(D.ImmutableRevisionError):
        D._guard_immutable(a, {**tampered, "transitions": a["transitions"]})
    with pytest.raises(D.ImmutableRevisionError):
        D._guard_immutable({**a, "transitions": [{"x": 1}]}, {**a, "transitions": []})


def test_stale_transition_is_a_conflict(ws, proj):
    doc, a = reg(ws, proj, "03 Drawings/Native/bracket.idw")
    seen = a["_rev"]
    D.transition(ws, a["id"], "in_review")
    with pytest.raises(ConflictError):
        D.transition(ws, a["id"], "approved", expected_rev=seen)  # legal move, stale view


# ---------------------------------------------------------------- retrieval, restore, reports
def test_export_restore_and_compare(ws, proj, tmp_path):
    doc, a = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    b = _new_rev(ws, doc, b"IPT v2 longer", "v2")
    out = D.export_revision(ws, a["id"], tmp_path / "bracket_A.ipt")
    assert out.read_bytes() == b"IPT v1"
    with pytest.raises(FileExistsError):
        D.export_revision(ws, a["id"], out)
    co = D.checkout(ws, doc["id"])
    D.restore_to_working(ws, doc["id"], co["token"], a["id"])
    assert D.working_path(ws, ws.get("document", doc["id"])).read_bytes() == b"IPT v1"
    c = D.checkin(ws, doc["id"], co["token"], "Revert thickness change", allow_unchanged=True)
    assert c["label"] == "C" and c["restored_from"] == a["id"] and c["sha256"] == a["sha256"]
    assert [r["label"] for r in D.history(ws, doc["id"])] == ["A", "B", "C"]  # nothing rewritten
    cmp = D.compare_revisions(ws, b["id"], c["id"])
    assert cmp["same_content"] is False and cmp["size_delta"] == len(b"IPT v1") - len(b"IPT v2 longer")
    assert D.compare_revisions(ws, a["id"], c["id"])["same_content"] is True


def test_drawing_register(ws, proj):
    doc, a = reg(ws, proj, "03 Drawings/Native/bracket.idw", number="DWG-100", title="Bracket", owner="sato")
    reg(ws, proj, "02 CAD/Parts/bracket.ipt")  # not a drawing
    for state in ("in_review", "approved", "released"):
        D.transition(ws, a["id"], state)
    rows = D.drawing_register(ws, proj["id"])
    assert len(rows) == 1
    r = rows[0]
    assert (r["number"], r["revision"], r["state"], r["released_revision"], r["customer"]) == \
        ("DWG-100", "A", "Released", "A", "Acme")
    assert r["checker"] and r["approver"]
    csv_text = D.drawing_register_csv(rows)
    assert csv_text.splitlines()[0].startswith("number,title,project")


def test_document_metadata_update_and_conflict(ws, proj):
    doc, _ = reg(ws, proj, "02 CAD/Parts/bracket.ipt")
    seen = doc["_rev"]
    D.update_document(ws, doc["id"], expected_rev=seen, title="Mounting bracket")
    with pytest.raises(ConflictError):
        D.update_document(ws, doc["id"], expected_rev=seen, title="Other")
    with pytest.raises(D.DocumentError):
        D.update_document(ws, doc["id"], rel_path="elsewhere.ipt")


def test_workspace_verify_clean_after_full_workflow(ws, proj):
    doc, a = reg(ws, proj, "03 Drawings/Native/bracket.idw")
    b = _new_rev(ws, doc, b"IDW v2", "v2")
    for state in ("in_review", "approved", "released"):
        D.transition(ws, b["id"], state)
    assert ws.verify()["problems"] == [] and D.verify_revisions(ws) == []


# ---------------------------------------------------------------- local index
def test_index_tracks_documents_revisions_and_checkouts(ws, proj, cache_dir):
    from revisionworx.index import open_index
    with open_index(ws, cache_dir) as idx:
        doc, a = reg(ws, proj, "02 CAD/Parts/bracket.ipt", number="P-7", title="Bracket")
        co = D.checkout(ws, doc["id"])
        idx.sync()
        row = idx.documents(proj["id"])[0]
        assert (row["number"], row["latest_label"], row["checked_out_by"]) == ("P-7", "A", co["owner"]["user"])
        (ws.folder / "02 CAD/Parts/bracket.ipt").write_bytes(b"v2")
        D.checkin(ws, doc["id"], co["token"], "v2")
        assert idx.sync()["mode"] == "incremental"
        row = idx.documents(text="bracket")[0]
        assert row["latest_label"] == "B" and row["checked_out_by"] is None
        assert [r["label"] for r in idx.revisions(doc["id"])] == ["A", "B"]
        incremental = idx.documents()
        idx.rebuild()
        assert idx.documents() == incremental
        assert idx.documents_by_content(a["sha256"])[0]["label"] == "A"


# ---------------------------------------------------------------- CLI
def test_cli_document_workflow(tmp_path, capsys):
    import json
    from revisionworx import cli
    share = tmp_path / "share"
    (share / "Eng").mkdir(parents=True)
    w = ["-w", str(share), "--cache-dir", str(tmp_path / "cache"), "--state-dir", str(tmp_path / "state")]

    def run(*a):
        code = cli.main([*w, *a])
        out = capsys.readouterr()
        return code, out.out, out.err

    run("init", str(share), "--name", "Docs")
    run("root", "add", "Eng", str(share / "Eng"))
    code, out, _ = run("--json", "project", "new", "--name", "Valve block", "--number", "J-9")
    p = json.loads(out)
    f = share / "Eng" / "Unassigned" / "J-9 Valve block" / "03 Drawings" / "Native" / "block.slddrw"
    f.write_bytes(b"DRW1")
    code, out, _ = run("--json", "doc", "register", p["id"], str(f), "--number", "D-1")
    assert code == 0
    doc = json.loads(out)["document"]
    rev_a = json.loads(out)["revision"]
    assert run("doc", "checkout", doc["id"])[0] == 0
    code, _, err = run("doc", "checkout", doc["id"])
    assert code == 2 and "already checked out" in err
    f.write_bytes(b"DRW2")
    code, out, _ = run("--json", "doc", "checkin", doc["id"], "-m", "Add section view")
    assert code == 0 and json.loads(out)["label"] == "B"
    rev_b = json.loads(out)
    for state in ("in_review", "approved", "released"):
        assert run("rev", "transition", rev_b["id"], state)[0] == 0
    code, out, _ = run("--json", "doc", "show", doc["id"], "--hash")
    shown = json.loads(out)
    assert [r["label"] for r in shown["history"]] == ["A", "B"] and shown["status"]["state"] == "unchanged"
    assert "token" not in json.dumps(shown["checkout"] or {})
    code, out, _ = run("doc", "register-report", p["id"])
    assert "D-1" in out and "Released" in out
    assert run("rev", "export", rev_a["id"], str(tmp_path / "a.slddrw"))[0] == 0
    assert (tmp_path / "a.slddrw").read_bytes() == b"DRW1"
    assert run("doc", "verify")[0] == 0
    code, out, _ = run("--json", "doc", "list", "--project", p["id"])
    assert json.loads(out)[0]["released_label"] == "B"
    f.write_bytes(b"edited without checkout")
    code, out, _ = run("--json", "doc", "show", doc["id"])
    assert json.loads(out)["status"]["state"] == "modified-externally"
    assert run("verify")[0] == 0
