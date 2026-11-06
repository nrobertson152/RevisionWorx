# SPDX-License-Identifier: GPL-3.0-or-later
"""Does this folder behave the way a team workspace needs? (ADR-024)

:func:`check_folder` tests the file operations from this one computer, and team use isn't turned on unless they pass. It can
show that a share is unsuitable. It can't show that a share is safe between computers: only :func:`run_test`, run at the same
time on two or more computers, can give evidence of that, and :func:`read_report` says what it found.
"""
from __future__ import annotations

import json
import os
import platform
import secrets
import shutil
import socket
import threading
import time
from pathlib import Path

from . import atomicio, paths
from .atomicio import ExclusiveCreateError
from .shared import is_transient

TEST_FOLDER = ".revisionworx-share-test"
_RACE_THREADS = 4
_RACE_NAMES = 25
_PAD = 4096


def _check_read_back(base: Path) -> str:
    data = secrets.token_bytes(1024 * 1024)
    path = base / "read-back"
    atomicio.atomic_write_bytes(path, data)
    if atomicio.read_bytes(path) != data:
        raise OSError("a file read back differently from what was written")
    return "a written file reads back the same"


def _check_exclusive(base: Path) -> str:
    path = base / "exclusive"
    atomicio.publish_exclusive(path, b"first")
    try:
        atomicio.publish_exclusive(path, b"second")
    except ExclusiveCreateError:
        pass
    else:
        raise OSError("a second file was published under a name already taken: exclusive create isn't exclusive here")
    if atomicio.read_bytes(path) != b"first":
        raise OSError("a refused publish changed the file anyway")
    return "a name can only be taken once"


def _check_race(base: Path) -> str:
    folder = base / "race"
    folder.mkdir()
    winners: dict[int, list[int]] = {n: [] for n in range(_RACE_NAMES)}
    errors: list[str] = []
    guard = threading.Lock()
    start = threading.Barrier(_RACE_THREADS)

    def racer(me: int) -> None:
        try:
            start.wait(timeout=30)
            for n in range(_RACE_NAMES):
                try:
                    atomicio.publish_exclusive(folder / f"{n}", str(me).encode())
                except ExclusiveCreateError:
                    continue
                with guard:
                    winners[n].append(me)
        except Exception as exc:  # noqa: BLE001 - whatever it was, it's the finding
            with guard:
                errors.append(repr(exc))
    threads = [threading.Thread(target=racer, args=(i,), daemon=True) for i in range(_RACE_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    if errors:
        raise OSError(f"a race for a name failed: {errors[0]}")
    for n, won in winners.items():
        if len(won) != 1:
            raise OSError(f"{len(won)} clients won the same name in a race; it has to be exactly one")
        if atomicio.read_bytes(folder / f"{n}") != str(won[0]).encode():
            raise OSError("what's under a name isn't what its winner wrote")
    return f"{_RACE_THREADS} clients raced for {_RACE_NAMES} names, and each name had one winner"


def _check_replace(base: Path) -> str:
    path = base / "replace"
    atomicio.atomic_write_json(path, {"n": 0, "pad": "x" * _PAD})
    stop, torn, errors = threading.Event(), [], []

    def reader() -> None:
        while not stop.is_set():
            try:
                value = atomicio.read_json_object(path)
                if len(value.get("pad", "")) != _PAD:
                    torn.append(value)
            except ValueError as exc:
                torn.append(exc)
            except OSError as exc:
                errors.append(exc)
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        for n in range(1, 40):
            atomicio.atomic_write_json(path, {"n": n, "pad": "x" * _PAD})
    finally:
        stop.set()
        thread.join(timeout=30)
    if torn:
        raise OSError("a reader saw half of a replaced file")
    if errors:
        raise OSError(f"a file couldn't be read while it was being replaced: {errors[0]}")
    return "a reader only ever sees a whole file while it's being replaced"


def _check_rename_while_open(base: Path) -> str:
    path, other = base / "open-file", base / "open-file-moved"
    path.write_bytes(b"data")
    with atomicio._open_for_read(path):
        atomicio.retry_sharing(lambda: os.replace(path, other))
    if atomicio.read_bytes(other) != b"data":
        raise OSError("a file renamed while it was open changed")
    return "a file can be renamed while someone is reading it"


def _check_visible(base: Path) -> str:
    folder = base / "visible"
    folder.mkdir()
    atomicio.publish_exclusive(folder / "name", b"x")
    if "name" not in os.listdir(folder):
        raise OSError("a file just published isn't in the folder's listing")
    (folder / "name").unlink()
    if (folder / "name").exists():
        raise OSError("a file just deleted is still there")
    return "this computer sees its own changes at once"


_CHECKS = (("files read back", _check_read_back), ("exclusive create", _check_exclusive), ("racing for a name", _check_race),
           ("atomic replace", _check_replace), ("rename while open", _check_rename_while_open), ("seeing its own changes", _check_visible))


def check_folder(folder: Path) -> dict:
    """Try the file operations a team workspace relies on, in a temporary folder inside ``folder``, then remove it."""
    folder = Path(folder)
    base = folder / f".share-check-{secrets.token_hex(6)}"
    checks = []
    try:
        base.mkdir()
    except OSError as exc:
        checks.append({"name": "creating files", "ok": False, "detail": f"can't create a folder here: {exc.strerror or exc}"})
    else:
        try:
            for name, check in _CHECKS:
                try:
                    checks.append({"name": name, "ok": True, "detail": check(base)})
                except OSError as exc:
                    checks.append({"name": name, "ok": False, "detail": str(exc)})
        finally:
            shutil.rmtree(base, ignore_errors=True)
    return {"folder": str(folder), "ok": all(c["ok"] for c in checks), "checks": checks, "network": paths.is_network_location(folder),
            "scope": "from this one computer. It can show a share is unsuitable, not that it's safe between computers"}


# ---------------------------------------------------------------------------- evidence from several computers
def _make_shared_folder(folder: Path) -> None:
    """Make a folder that another computer may be making too. Having been told it exists, this client can still be told it doesn't for
    a few seconds, so this waits until the two agree."""
    give_up = time.monotonic() + 60
    while True:
        try:
            atomicio.make_dirs(folder)
            os.listdir(folder)
            return
        except OSError as exc:
            if not (isinstance(exc, FileNotFoundError) or is_transient(exc)) or time.monotonic() >= give_up:
                raise
            time.sleep(0.2)


def run_test(folder: Path, seconds: float = 60.0, runner: str | None = None, say=None) -> dict:
    """Race for numbered names against every other computer running this at the same time, the way journal entries are
    published, and write down what happened. Touches only its own folder inside ``folder``."""
    base = Path(folder) / TEST_FOLDER
    slots, runners = base / "slots", base / "runners"
    _make_shared_folder(slots)
    _make_shared_folder(runners)
    me = runner or f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(3)}"
    won: list[int] = []
    seen: dict[str, str] = {}
    problems: list[str] = []
    slowest_read, stale_listings, oldest_stale, busy_answers = 0.0, 0, 0.0, 0
    n = 1 + sum(1 for name in os.listdir(slots) if name.endswith(".json") and not name.startswith("."))
    started, last_said, checks = time.time(), 0.0, 0
    published_at: dict[int, float] = {}
    end = time.monotonic() + seconds
    pad = "x" * _PAD
    while time.monotonic() < end:
        name = f"{n:012d}.json"
        path = slots / name
        try:
            atomicio.publish_exclusive(path, json.dumps({"runner": me, "slot": n, "pad": pad}).encode())
            won.append(n)
            published_at[n] = time.monotonic()
        except OSError as exc:
            if not isinstance(exc, ExclusiveCreateError):
                if not is_transient(exc):
                    raise
                busy_answers += 1                    # the share said "try again": nothing was won, and the same name is tried again
                time.sleep(0.05)
                continue
            began = time.monotonic()
            while True:                              # the name is taken, so the file has to be readable, and whole
                try:
                    claim = atomicio.read_json_object(path)
                    break
                except OSError as read_error:
                    if not isinstance(read_error, FileNotFoundError) and not is_transient(read_error):
                        raise
                    busy_answers += 0 if isinstance(read_error, FileNotFoundError) else 1
                    if time.monotonic() - began > 60:
                        problems.append(f"slot {n}: taken, but still not readable after 60 seconds")
                        claim = None
                        break
                    time.sleep(0.05)
                except ValueError as exc:
                    problems.append(f"slot {n}: a torn or damaged file: {exc}")
                    claim = None
                    break
            slowest_read = max(slowest_read, time.monotonic() - began)
            if claim is not None:
                if claim.get("slot") != n or len(claim.get("pad", "")) != _PAD:
                    problems.append(f"slot {n}: the file isn't what its winner wrote")
                seen[str(n)] = str(claim.get("runner"))
        n += 1
        checks += 1
        if checks % 50 == 0:                         # how old can a listing be? Only ever a measurement: nothing relies on it.
            listed = set(os.listdir(slots))
            missing = [k for k in range(max(1, n - 50), n) if f"{k:012d}.json" not in listed]
            if missing:
                stale_listings += 1
                oldest = published_at.get(missing[0])
                if oldest:
                    oldest_stale = max(oldest_stale, time.monotonic() - oldest)
        if say and time.monotonic() - last_said > 5:
            say(f"{len(won)} names won, {len(seen)} lost to others")
            last_said = time.monotonic()
    result = {"runner": me, "host": socket.gethostname(), "platform": platform.platform(), "python": platform.python_version(),
              "started": started, "seconds": seconds, "won": won, "seen": seen, "problems": problems, "slowest_read": slowest_read,
              "stale_listings": stale_listings, "oldest_stale_listing": oldest_stale, "busy_answers": busy_answers}
    atomicio.atomic_write_json(runners / f"{secrets.token_hex(8)}.json", result)
    return read_report(folder)


def read_report(folder: Path) -> dict:
    """What every computer that ran the test saw. ``ok`` needs at least two computers, each having lost a race to another."""
    base = Path(folder) / TEST_FOLDER / "runners"
    results, unreadable = [], []
    for path in sorted(base.iterdir()) if base.is_dir() else []:
        if path.name.endswith(".json") and not path.name.startswith("."):
            try:
                results.append(atomicio.read_json_object(path))
            except (OSError, ValueError) as exc:
                unreadable.append(f"{path.name}: {exc}")
    problems = list(unreadable)
    winners: dict[str, list[str]] = {}
    for result in results:
        problems.extend(f"{result.get('host')}: {p}" for p in result.get("problems", []))
        for slot in result.get("won", []):
            winners.setdefault(str(slot), []).append(str(result.get("runner")))
    for slot, who in winners.items():
        if len(who) > 1:
            problems.append(f"slot {slot}: {len(who)} winners ({', '.join(who)}). Exclusive create isn't exclusive on this share, "
                            "so it isn't safe for team use")
    for result in results:
        for slot, runner in result.get("seen", {}).items():
            if winners.get(slot) and runner not in winners[slot]:
                problems.append(f"slot {slot}: {result.get('host')} read {runner} as the winner, but {winners[slot][0]} won it")
    hosts = sorted({str(r.get("host")) for r in results})
    contended = {str(r.get("host")) for r in results if r.get("seen")}
    cross = len(hosts) >= 2 and contended == set(hosts)
    if not results:
        verdict = "nothing has been run here yet"
    elif problems:
        verdict = "FAILED: this share isn't safe for team use"
    elif cross:
        verdict = f"passed between {len(hosts)} computers"
    else:
        verdict = "no problems, but that isn't evidence yet: it takes two or more computers, running at the same time"
    return {"ok": cross and not problems, "cross_computer": cross, "verdict": verdict, "hosts": hosts, "runners": len(results),
            "slots_won": sum(len(r.get("won", [])) for r in results), "races_lost": sum(len(r.get("seen", {})) for r in results),
            "slowest_read_seconds": max([float(r.get("slowest_read", 0.0)) for r in results], default=0.0),
            "stale_listings": sum(int(r.get("stale_listings", 0)) for r in results),
            "oldest_stale_listing_seconds": max([float(r.get("oldest_stale_listing", 0.0)) for r in results], default=0.0),
            "busy_answers": sum(int(r.get("busy_answers", 0)) for r in results),
            "problems": problems}
