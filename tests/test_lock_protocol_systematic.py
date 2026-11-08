# SPDX-License-Identifier: GPL-3.0-or-later
"""Systematic schedule exploration of the writer protocol: every schedule within a bound, not a random sample.

Random schedules miss rare orderings, and hand-written interleavings only cover what someone thought of. This is
stateless model checking with context bounding (CHESS, Musuvathi and Qadeer): actors run the real code one at a time, and
a *plan* says at which filesystem operation to switch to another actor or kill the current one. Exploration starts from the
plan with no interruptions and adds one interruption at a time, at every point the previous run actually reached.
Most concurrency defects need very few interruptions, which is what makes the bound useful.

Limits: threads stand in for processes, and only lock, journal and materialisation operations are switch points. A run
within the budget is evidence about those schedules only. By default the deepest level is a seeded sample so CI stays
quick; ``REVISIONWORX_SYSTEMATIC_BUDGET=0`` explores every schedule within the bounds.
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import threading
import uuid
from pathlib import Path

import pytest

from revisionworx import atomicio, documents as D, projects
from revisionworx.content import ContentStore
from revisionworx.projects import NewProject
from revisionworx.workspace import REV_FIELD, RecordPut, TxidReuseError, Workspace, WorkspaceError
from test_lock_protocol_schedules import RID, check_invariants

KILL = "<kill>"
TXID = "cd" * 16
BUDGET = int(os.environ.get("REVISIONWORX_SYSTEMATIC_BUDGET", "500"))
WAIT = 60


class Killed(BaseException):
    pass


class PlannedRun:
    """One deterministic schedule. Runs an actor until the plan says otherwise or it finishes."""

    def __init__(self, order: list[str], plan: dict[int, str]):
        self.plan = plan
        self.live = list(order)
        self.baton = {name: threading.Semaphore(0) for name in order}
        self.dead: set[str] = set()
        self.step = 0
        self.trace: list[tuple[int, str, tuple[str, ...]]] = []
        self.stuck = False

    def _wait(self, name: str) -> None:
        if not self.baton[name].acquire(timeout=WAIT):
            self.stuck = True
            raise Killed("handoff timed out")

    def point(self) -> None:
        name = threading.current_thread().name
        if name not in self.baton:
            return
        if name in self.dead:
            raise Killed
        self.step += 1
        self.trace.append((self.step, name, tuple(self.live)))
        action = self.plan.get(self.step)
        if action == KILL:
            self.dead.add(name)
            raise Killed
        if action in self.live and action != name:
            self.baton[action].release()
            self._wait(name)
            if name in self.dead:
                raise Killed

    def finished(self) -> None:
        self.live.remove(threading.current_thread().name)
        if self.live:
            self.baton[self.live[0]].release()


def run_plan(scenario: "Scenario", plan: dict[int, str], monkeypatch) -> tuple[list, str | None]:
    root = scenario.fresh_copy()
    run = PlannedRun(list(scenario.actors), plan)
    failures: list[str] = []
    counter = [0]

    def next_uuid():
        counter[0] += 1
        return uuid.UUID(int=counter[0])

    def body(fn):
        try:
            run._wait(threading.current_thread().name)
            fn(root)
        except Killed:
            pass
        except BaseException as exc:                # noqa: BLE001 - anything else is a finding
            failures.append(f"{threading.current_thread().name}: {type(exc).__name__}: {exc}")
        finally:
            run.finished()

    threads = [threading.Thread(target=body, args=(fn,), name=name, daemon=True) for name, fn in scenario.actors.items()]

    def switch_point(fn, *watched):
        def wrapper(path, *args, **kwargs):
            if not watched or any(part in Path(path).parts for part in watched):
                run.point()
            return fn(path, *args, **kwargs)
        return wrapper

    with monkeypatch.context() as patch:
        for name in ("replace", "rename", "link", "unlink"):
            patch.setattr(os, name, switch_point(getattr(os, name), "locks", "journal", "records", "state.json"))
        patch.setattr(os, "scandir", switch_point(os.scandir, "journal"))
        patch.setattr(atomicio, "read_json", switch_point(atomicio.read_json, "locks"))
        patch.setattr(os, "fsync", lambda fd: None)  # speed only; power loss is not modelled here
        patch.setattr(uuid, "uuid4", next_uuid)     # a failing plan can be replayed exactly
        for t in threads:
            t.start()
        run.baton[run.live[0]].release()
        for t in threads:
            t.join(WAIT)
    if any(t.is_alive() for t in threads) or run.stuck:
        return run.trace, "scheduler stuck"
    if failures:
        return run.trace, f"unexpected: {failures}"
    try:
        scenario.check(root)
    except AssertionError as exc:
        return run.trace, str(exc)
    return run.trace, None


def explore(scenario: "Scenario", monkeypatch, preemptions: int, kills: int, budget: int = 0) -> dict:
    """Breadth first by number of interruptions, so shallow schedules always run. Returns what it covered and,
    if a schedule broke an invariant, the plan that did it, which reruns exactly."""
    rng = random.Random(20261102)
    level: list[dict[int, str]] = [{}]
    runs, depth = 0, 0
    sampled = False
    while level:
        following: list[dict[int, str]] = []
        for plan in level:
            trace, problem = run_plan(scenario, plan, monkeypatch)
            runs += 1
            if problem:
                return {"runs": runs, "plan": plan, "problem": problem, "sampled": sampled, "depth": depth}
            last = max(plan) if plan else 0
            used_kills = sum(1 for v in plan.values() if v == KILL)
            used_preempt = len(plan) - used_kills
            for step, who, live in trace:
                if step <= last:
                    continue
                if used_preempt < preemptions:
                    following.extend({**plan, step: other} for other in live if other != who)
                if used_kills < kills:
                    following.append({**plan, step: KILL})
        depth += 1
        if budget and runs + len(following) > budget:
            rng.shuffle(following)
            following = following[:max(0, budget - runs)]
            sampled = True
        level = following
    return {"runs": runs, "plan": None, "problem": None, "sampled": sampled, "depth": depth}


class Scenario:
    def __init__(self, base: Path, prepare, actors: dict, check):
        self.base, self.actors, self.check = base, actors, check
        self.template = base / "template"
        prepare(self.template)
        self.n = 0
        self.last: Path | None = None

    def fresh_copy(self) -> Path:
        if self.last is not None:
            shutil.rmtree(self.last, ignore_errors=True)
        self.n += 1
        self.last = self.base / f"run{self.n}"
        shutil.copytree(self.template, self.last)
        return self.last


# ---------------------------------------------------------------------------------------- actors
def writer(root: Path) -> None:
    try:
        ws = Workspace.open(root, writable=True)
    except WorkspaceError:
        return
    try:
        current = ws.get("root", RID)
        ws.commit("work", [RecordPut("root", RID, {"name": "x"}, expect_rev=current[REV_FIELD] if current else 0)])
    except WorkspaceError:
        pass
    finally:
        try:
            ws.close()
        except WorkspaceError:
            pass


def admin(root: Path) -> None:
    holder = Workspace.read_writer_lock(root)
    if holder and holder.get("token"):
        try:
            Workspace.break_writer_lock(root, holder["token"], "exploration")
        except WorkspaceError:
            pass


# ---------------------------------------------------------------------------------------- starting states
def empty_workspace(root: Path) -> None:
    with Workspace.create(root, "explore"):
        pass


def stranded_revoked_lock(root: Path) -> None:
    empty_workspace(root)
    holder = Workspace.open(root, writable=True)
    Workspace._publish_revocation(holder.meta_dir, atomicio.read_json(holder.meta_dir / "locks" / "writer.lock"),
                                  reason="stranded", evidence="x")
    holder._lock_token = None
    holder.close()


def orphaned_live_lock(root: Path) -> None:
    empty_workspace(root)
    atomicio.atomic_write_json(root / ".revisionworx" / "locks" / ".releasing-feed",
                               {"token": "b" * 32, "user": "u", "host": "h", "pid": 1})


LOCK_SCENARIOS = {
    "two_writers_and_an_admin": (empty_workspace, {"W1": writer, "W2": writer, "Admin": admin}),
    "writers_racing_over_a_revoked_lock": (stranded_revoked_lock, {"D": writer, "B": writer, "C": writer}),
    "writers_racing_over_an_orphan": (orphaned_live_lock, {"W1": writer, "W2": writer}),
}


@pytest.mark.parametrize("name", list(LOCK_SCENARIOS))
def test_no_schedule_within_two_interruptions_breaks_authority(tmp_path, monkeypatch, name):
    prepare, actors = LOCK_SCENARIOS[name]
    report = explore(Scenario(tmp_path, prepare, actors, check_invariants), monkeypatch, preemptions=2, kills=0,
                     budget=BUDGET)
    print(f"SCHEDULES {name} preemptions=2 kills=0: {report['runs']} runs, sampled={report['sampled']}")
    assert report["problem"] is None, report
    assert report["runs"] > 1


@pytest.mark.parametrize("name", list(LOCK_SCENARIOS))
def test_no_schedule_with_a_switch_and_a_death_breaks_authority(tmp_path, monkeypatch, name):
    prepare, actors = LOCK_SCENARIOS[name]
    report = explore(Scenario(tmp_path, prepare, actors, check_invariants), monkeypatch, preemptions=1, kills=1,
                     budget=BUDGET)
    print(f"SCHEDULES {name} preemptions=1 kills=1: {report['runs']} runs, sampled={report['sampled']}")
    assert report["problem"] is None, report
    assert report["runs"] > 1


# ---------------------------------------------------------------------------------------- check-in, exactly once
def test_no_schedule_makes_a_checkin_take_effect_twice_or_lie(tmp_path, monkeypatch):
    """A check-in, an administrator breaking its lock, and a retry with the same txid."""
    state: dict = {}

    def prepare(root: Path) -> None:
        (root / "Engineering").mkdir(parents=True)
        with Workspace.create(root, "explore") as ws:
            projects.add_root(ws, "Engineering", root / "Engineering")
            project = projects.create_project(ws, NewProject(name="HPU", number="J-1", customer="Acme"))
            path = projects.project_folder(ws, project) / "02 CAD" / "Parts" / "bracket.ipt"
            path.write_bytes(b"v1")
            doc, _ = D.register_document(ws, project["id"], path)
            state["doc"], state["token"] = doc["id"], D.checkout(ws, doc["id"])["token"]
            state["rel"] = path.relative_to(root)
            path.write_bytes(b"v2")

    def attempt(root: Path) -> None:
        try:
            ws = Workspace.open(root, writable=True)
        except WorkspaceError:
            return
        try:
            D.checkin(ws, state["doc"], state["token"], "edit", txid=TXID)
        except (TxidReuseError, D.NotCheckedOut, D.TokenMismatch, D.StaleCheckout, D.NoChanges):
            raise                                   # the same request must never be told any of these
        except WorkspaceError:
            pass                                    # refused: lost the lock, or busy. Nothing happened
        finally:
            try:
                ws.close()
            except WorkspaceError:
                pass

    def check(root: Path) -> None:
        with Workspace.open(root) as ws:
            doc = ws.get("document", state["doc"])
            chain = D.revisions_of(ws, state["doc"])
            effective = [e for e in ws.entries_after(0) if e["txid"] == TXID]
            outcome = ws.transaction_outcome(TXID)["state"]
            assert doc["rev_count"] == len(chain) <= 2, f"{doc['rev_count']} revisions"
            assert len(effective) <= 1, "one txid took effect twice"
            assert (outcome == "committed") == bool(effective), f"outcome says {outcome}"
            assert (doc["rev_count"] == 2) == bool(effective), "a revision without its transaction"
            assert (ws.get("checkout", state["doc"]) is None) == bool(effective), "checkout and revision disagree"
            if effective:
                assert ContentStore(ws).verify(chain[-1]["sha256"]), "the revision's content is gone"
                assert chain[-1]["sha256"] == hashlib.sha256(b"v2").hexdigest()

    actors = {"First": attempt, "Admin": admin, "Retry": attempt}
    report = explore(Scenario(tmp_path, prepare, actors, check), monkeypatch, preemptions=2, kills=0, budget=BUDGET)
    print(f"SCHEDULES checkin preemptions=2 kills=0: {report['runs']} runs, sampled={report['sampled']}")
    assert report["problem"] is None, report
    report = explore(Scenario(tmp_path / "k", lambda r: (r.parent.mkdir(exist_ok=True), prepare(r))[1], actors, check),
                     monkeypatch, preemptions=1, kills=1, budget=BUDGET)
    print(f"SCHEDULES checkin preemptions=1 kills=1: {report['runs']} runs, sampled={report['sampled']}")
    assert report["problem"] is None, report


# ---------------------------------------------------------------------------------------- can this find anything?
def test_exploration_finds_the_stale_cleanup_defect_when_it_is_put_back(tmp_path, monkeypatch):
    """Mutation check. The defect of d2b7a1c: a stale decision moves whatever is at writer.lock to evidence."""
    def stale_move(meta, planned_evidence, reason):
        locks = meta / "locks"
        try:
            os.replace(locks / "writer.lock", locks / next(iter(planned_evidence.values())))
        except FileNotFoundError:
            pass
    monkeypatch.setattr(Workspace, "_displace_writer_lock", staticmethod(stale_move))
    prepare, actors = LOCK_SCENARIOS["writers_racing_over_a_revoked_lock"]
    report = explore(Scenario(tmp_path, prepare, actors, check_invariants), monkeypatch, preemptions=2, kills=0)
    assert report["problem"], f"no schedule found it in {report['runs']} runs"
    assert report["plan"], "it should take an interleaving to show"


def test_exploration_finds_missing_fencing_when_it_is_removed(tmp_path, monkeypatch):
    """Mutation check: if revoked writers were no longer fenced, an interleaving must show two writers."""
    monkeypatch.setattr(Workspace, "is_fenced", lambda self, entry: False)
    real = Workspace._journal_view

    def no_revocations(self, *args, **kwargs):          # look= and strict= pass through
        view = real(self, *args, **kwargs)
        view["revoked"] = set()
        view["revocations"] = []
        return view
    monkeypatch.setattr(Workspace, "_journal_view", no_revocations)
    prepare, actors = LOCK_SCENARIOS["two_writers_and_an_admin"]
    report = explore(Scenario(tmp_path, prepare, actors, check_invariants), monkeypatch, preemptions=2, kills=0)
    assert report["problem"], f"no schedule found it in {report['runs']} runs"


def test_a_failing_plan_replays_exactly(tmp_path, monkeypatch):
    prepare, actors = LOCK_SCENARIOS["two_writers_and_an_admin"]
    scenario = Scenario(tmp_path, prepare, actors, check_invariants)
    first, _ = run_plan(scenario, {3: "W2"}, monkeypatch)
    second, _ = run_plan(scenario, {3: "W2"}, monkeypatch)
    assert first == second and len(first) > 3
