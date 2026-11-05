# SPDX-License-Identifier: GPL-3.0-or-later
"""The alpha journey with a real process, a real X display and real keystrokes, keyboard only.

Not collected by pytest. Needs Xvfb, xdotool, openbox and ImageMagick, and only ever touches a temp folder:

    xvfb-run -a -s "-screen 0 1366x768x24" python tests/e2e_x11_walkthrough.py --shots /tmp/shots

Outcomes are checked independently of the GUI, through the CLI and the files on disk. This finds what offscreen
tests cannot (focus, real dialogs, typed input), but it is not a person using the app, and says nothing about
Windows or native file dialogs.
"""
import argparse
import json
import os
import subprocess as sp
import sys
import tempfile
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--shots", type=Path, required=True)
args = ap.parse_args()
args.shots.mkdir(parents=True, exist_ok=True)
T = Path(tempfile.mkdtemp(prefix="rwx-walk"))
HOME = T / "home"
HOME.mkdir()
SRC = T / "bracket.ipt"
SRC.write_bytes(b"version one")
EXPORTED = T / "exported.ipt"
WS = HOME / "RevisionWorx Projects"
WORKING = WS / "Projects" / "T-1 Test Bracket" / "bracket.ipt"
ENV = {**os.environ, "HOME": str(HOME), "XDG_CONFIG_HOME": str(HOME / ".config"),
       "XDG_STATE_HOME": str(HOME / ".state"), "XDG_CACHE_HOME": str(HOME / ".cache"),
       "QT_QPA_PLATFORM": "xcb", "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
ENV.pop("REVISIONWORX_WORKSPACE", None)
shot_no = 0
current_app = None


def xdo(*a) -> str:
    return sp.run(["xdotool", *a], capture_output=True, text=True, env=ENV).stdout.strip()


def shot(name: str) -> None:
    global shot_no
    shot_no += 1
    sp.run(["import", "-window", "root", str(args.shots / f"{shot_no:02d}_{name}.png")], env=ENV, check=False)


def fail(message: str):
    shot("FAILED")
    print(f"FAILED: {message}\nlast window: {xdo('getactivewindow', 'getwindowname')}", flush=True)
    if current_app:
        current_app.kill()
        print(current_app.communicate()[0][-2000:])
    sys.exit(1)


def wait_for(what: str, check, timeout: float = 30):
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    fail(f"timed out waiting for {what}")


def window_is(text: str):
    wait_for(f"window '{text}'", lambda: text in xdo("getactivewindow", "getwindowname"))


def key(*keys):
    for k in keys:
        xdo("key", "--clearmodifiers", k)
        time.sleep(0.35)


def typed(text: str):
    xdo("type", "--clearmodifiers", "--delay", "15", text)
    time.sleep(0.4)


def cli(*a) -> str:
    r = sp.run([sys.executable, "-m", "revisionworx", "-w", str(WS), *a], capture_output=True, text=True, env=ENV)
    return r.stdout + r.stderr


def doc_list() -> str:
    return cli("doc", "list")


def rev_is(label: str):
    wait_for(f"revision {label}", lambda: f"rev {label} " in doc_list())


def checked_out(expected: bool):
    wait_for(f"checked out = {expected}", lambda: ("OUT:" in doc_list()) == expected)


def check_out_by_keyboard():
    """Ctrl+K is refused while a background job (an export, say) has not delivered its result yet: the file
    can already be on disk by then. A person would press it again; so does this."""
    end = time.time() + 30
    while time.time() < end:
        key("ctrl+k")
        for _ in range(20):
            if "OUT:" in doc_list():
                return
            time.sleep(0.1)
    fail("timed out waiting for checked out = True")


def launch():
    global current_app
    current_app = sp.Popen([sys.executable, "-m", "revisionworx.gui.app"], env=ENV, stdout=sp.PIPE, stderr=sp.STDOUT,
                           text=True)
    window_is("RevisionWorx")
    time.sleep(1)


def quit_app():
    global current_app
    window_is("RevisionWorx 1.")
    key("ctrl+q")
    try:
        code = current_app.wait(15)
    except sp.TimeoutExpired:
        fail("the app did not quit")
    if code != 0:
        fail(f"exit code {code}")
    output = current_app.stdout.read()
    if "Traceback" in output:
        fail(f"traceback from the app:\n{output}")
    current_app = None


def check_in(description: str):
    key("ctrl+shift+k")
    window_is("Check in")
    key("alt+d")
    typed(description)
    key("alt+i")  # the dialog's Check in button
    window_is("RevisionWorx 1.")


sp.Popen(["openbox"], env=ENV, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
time.sleep(1)

print("1. create a project on the start page", flush=True)
launch()
shot("start")
key("alt+n")
typed("Test Bracket")
key("Tab")
typed("T-1")
key("Return")
wait_for("the project folder", (WORKING.parent).is_dir)
window_is("RevisionWorx Projects")

print("2. import by typing a path; the title must be the file name", flush=True)
key("ctrl+i")
window_is("Import file")
typed(str(SRC))
shot("import_typed")
key("alt+i")
rev_is("A")
key("Escape")
window_is("RevisionWorx 1.")
doc_id = doc_list().split()[0]
if json.loads(sp.run([sys.executable, "-m", "revisionworx", "-w", str(WS), "--json", "doc", "show", doc_id],
                     capture_output=True, text=True, env=ENV).stdout)["document"]["title"] != "bracket":
    fail("wrong document title")
if SRC.read_bytes() != b"version one":
    fail("the source file changed")

print("3. check out, edit, check in; an empty description is refused", flush=True)
check_out_by_keyboard()
WORKING.write_bytes(b"version two")
key("F5")
key("ctrl+shift+k")
window_is("Check in")
key("alt+i")
time.sleep(0.5)
shot("checkin_refused")
if "Check in" not in xdo("getactivewindow", "getwindowname") or "rev A " not in doc_list():
    fail("an empty description was accepted")
key("Escape")
window_is("RevisionWorx 1.")
check_in("Thicker flange for load case B")
rev_is("B")
checked_out(False)
shot("checked_in")

print("4. export, then replace through the save dialog's own prompt", flush=True)
key("alt+e")
window_is("Export revision")
key("ctrl+a")
typed(str(EXPORTED))
key("Return")
wait_for("the exported file", EXPORTED.exists)
window_is("RevisionWorx 1.")
if EXPORTED.read_bytes() != b"version two":
    fail("wrong exported bytes")
EXPORTED.write_bytes(b"old")
key("alt+e")
window_is("Export revision")
key("ctrl+a")
typed(str(EXPORTED))
key("Return")
time.sleep(0.8)
shot("export_replace_prompt")
key("alt+y")
wait_for("the replaced file", lambda: EXPORTED.read_bytes() == b"version two")
window_is("RevisionWorx 1.")

print("5. Tab reaches the history, then restore an older revision", flush=True)
check_out_by_keyboard()
key("ctrl+shift+f")
key(*["Tab"] * 15)  # filter, Import, Register, 3 filter chips, table, Check in, Cancel, Open file, folder, Export,
#                    state x2, Compare
key("Down")
key("alt+t")
window_is("Restore earlier")
shot("restore_prompt")
key("alt+y")
wait_for("the restored bytes", lambda: WORKING.read_bytes() == b"version one")
window_is("RevisionWorx 1.")

print("6. kill -9 while holding the checkout; restart and check in", flush=True)
current_app.kill()
current_app.wait()
current_app = None
launch()
shot("after_kill9")
check_in("Restored the first version")
rev_is("C")
checked_out(False)

print("7. a quit and restart keeps the history; a missing file is shown", flush=True)
quit_app()
launch()
WORKING.unlink()
key("F5")
time.sleep(0.8)
shot("missing_file")
WORKING.write_bytes(b"version one")
key("F5")

print("8. a recovery block found while writing gives the read-only state; repair and F5 clear it", flush=True)
orphan = WS / ".revisionworx" / "locks" / ".releasing-deadbeef"
orphan.write_text("{}")
key("ctrl+k")
wait_for("the error dialog", lambda: xdo("getactivewindow", "getwindowname") == "RevisionWorx")
shot("block_dialog")
key("Return")
window_is("RevisionWorx 1.")
time.sleep(0.5)
shot("block_read_only")
key("ctrl+k")  # disabled now: no write and no second dialog
time.sleep(0.8)
if "RevisionWorx 1." not in xdo("getactivewindow", "getwindowname") or "OUT:" in doc_list():
    fail("the read-only state still acted on Ctrl+K")
if "Cleared" not in cli("lock", "clear-orphan", orphan.name, "--reason", "walkthrough"):
    fail("clear-orphan failed")
key("F5")
time.sleep(0.8)

print("9. a lock left by a crash: the error's own advice recovers", flush=True)
sp.run([sys.executable, "-c", "import os, sys; from pathlib import Path; from revisionworx.workspace import Workspace; "
        "Workspace.open(Path(sys.argv[1]), writable=True); os._exit(9)", str(WS)], env=ENV, check=False)
key("ctrl+k")
wait_for("the error dialog", lambda: xdo("getactivewindow", "getwindowname") == "RevisionWorx")
shot("lock_dialog")
key("Return")
window_is("RevisionWorx 1.")
if "OUT:" in doc_list():
    fail("a locked workspace accepted a checkout")
token = json.loads(cli("lock", "show"))["token"]
if "Broke" not in cli("lock", "break", "--token", token, "--reason", "session crashed"):
    fail("lock break failed")
key("ctrl+k")
checked_out(True)
shot("after_break")
quit_app()
print(f"WALKTHROUGH PASSED ({shot_no} screenshots in {args.shots})", flush=True)
