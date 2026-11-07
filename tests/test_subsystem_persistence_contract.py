# SPDX-License-Identifier: GPL-3.0-or-later
"""Core side of the subsystem persistence contract (CORE_API §8; requested by Claude D on PR #7 for
change control and by Claude C, A10, for BOM).

Subsystems own record kinds via ``register_kind`` and write only through ``Workspace.commit``. The core
guarantees asserted here are the ones they rely on:
  * a process that never imported the subsystem reads, recovers and verifies its entries, including an
    entry its writer committed and then died before applying;
  * such a process cannot *write* that kind (nothing is published);
  * ``transaction_outcome`` answers for a subsystem txid after the crash.
The crash is a real child process killed with ``os._exit``, on every OS CI runs.
"""
import multiprocessing as mp

import pytest

import proc_helpers
from revisionworx import workspace as core
from revisionworx.workspace import RecordPut, Workspace

KIND = "subsysx"                       # test-only kind; never registered in this (parent) process
RID = "sbx_" + "c" * 32
TXID = "d" * 32


def _journal(root):
    return sorted(p.name for p in (root / ".revisionworx" / "journal").iterdir())


@pytest.fixture
def crashed(tmp_path):
    root = tmp_path / "w"
    with Workspace.create(root, "t"):
        pass
    ctx = mp.get_context("spawn")
    p = ctx.Process(target=proc_helpers.subsystem_commit_then_die, args=(str(root), KIND, RID, TXID))
    p.start()
    p.join(60)
    assert p.exitcode == 3, f"child did not die at the crash point (exit {p.exitcode})"
    return root


def test_unregistered_reader_sees_and_recovers_a_subsystem_entry_its_writer_died_applying(crashed):
    root = crashed
    assert KIND not in core.KINDS                           # this process never imported the subsystem
    with Workspace.open(root) as ro:                        # read-only: the journal is the answer
        assert ro.get(KIND, RID)["name"] == "from subsystem"
        assert set(ro.list(KIND)) == {RID}
        assert ro.health()["ok"] and ro.health()["pending"] >= 1
        assert ro.transaction_outcome(TXID)["state"] == "committed"
    holder = Workspace.read_writer_lock(root)               # the dead writer's lock: an admin breaks it
    Workspace.break_writer_lock(root, holder["token"], "subsystem writer process died (test)")
    with Workspace.open(root, writable=True) as ws:         # recovery applies the entry, kind-agnostic
        assert ws.get(KIND, RID)["name"] == "from subsystem"
        h = ws.health()
        assert h["ok"] and h["pending"] == 0, h
        assert [p for p in ws.verify()["problems"] if p.get("severity") != "info"] == []
    assert (root / ".revisionworx" / "records" / KIND / f"{RID}.json").exists()
    from revisionworx.index import open_index                # the local index skips kinds it does not own
    with Workspace.open(root) as ro:
        idx = open_index(ro, cache_dir=root.parent / "cache")
        idx.close() if hasattr(idx, "close") else None


def test_unregistered_writer_cannot_write_the_kind_and_publishes_nothing(crashed):
    root = crashed
    Workspace.break_writer_lock(root, Workspace.read_writer_lock(root)["token"], "test")
    with Workspace.open(root, writable=True) as ws:
        before = _journal(root)
        with pytest.raises(ValueError, match="unknown record kind"):
            ws.commit("x", [RecordPut(KIND, RID, {"name": "overwrite"}, expect_rev=1)])
        assert _journal(root) == before
        assert ws.get(KIND, RID)["name"] == "from subsystem"


def test_proposed_subsystem_kinds_fit_the_core_rules_and_do_not_collide():
    """The kinds named in PR #20 and PR #21 (A10). A new kind must be added here and to CORE_API §8."""
    owners = {
        "core": {"root", "project", "template", "settings", "audit"},
        "documents": {"doctype", "document", "revision", "checkout", "lifecycle"},
        "bom (PR #21)": {"part", "partnum", "bomline", "bomstruct", "partseq", "partrule"},
        "change control (PR #20)": {"changereq", "changeord", "changeevt", "changenum", "changecfg"},
        "units (ADR-025)": {"unit", "unitevt", "unitkey", "buildstd", "uniteff", "unitcmp"},
    }
    seen: dict[str, str] = {}
    for owner, kinds in owners.items():
        for k in kinds:
            assert core._KIND_RE.match(k), (owner, k)
            assert k not in seen, f"{k} claimed by {seen.get(k)} and {owner}"
            seen[k] = owner
    import revisionworx.documents  # noqa: F401  (registers its kinds)
    assert owners["core"] | owners["documents"] <= core.KINDS
