"""Drive only the tested application's window on a private Xvfb display."""
import os
import json
import ast
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
if os.environ.get("GITHUB_PRIVATE_XVFB") != "1":
    raise SystemExit("Use the window-test target on a private Xvfb display")
output = ROOT / "build/screenshots"
output.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, GITHUB_CLIENT_API=str(ROOT / "tests/fixture_api.py"))
process = subprocess.Popen([str(ROOT / "build/github-desktop")], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def xdo(*args):
    return subprocess.check_output(["xdotool", *map(str, args)], text=True).strip()


def click(x, y):
    xdo("mousemove", "--window", window, int(x), int(y))
    xdo("click", "1")
    time.sleep(0.5)


def shot(name):
    subprocess.run(["import", "-window", window, str(output / name)], check=True)


def state(view=None):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        raw = subprocess.check_output(["xprop", "-id", window, "_HARMONY_APP_STATE"], text=True)
        if " = " in raw:
            value = json.loads(ast.literal_eval(raw.split(" = ", 1)[1].strip()))
            if not value["loading"] and (view is None or value["view"] == view):
                return value
        time.sleep(0.1)
    raise AssertionError("Native state did not reach " + str(view))


def action(label):
    value = state()
    control = next(c for c in value["controls"] if c["label"] == label and c["enabled"])
    click(control["x"] + control["width"] / 2, control["y"] + control["height"] / 2)


try:
    deadline = time.monotonic() + 10
    window = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(process.stderr.read().decode())
        found = subprocess.run(["xdotool", "search", "--onlyvisible", "--pid", str(process.pid)], capture_output=True, text=True)
        if found.returncode == 0:
            window = found.stdout.splitlines()[0]
            break
        time.sleep(0.1)
    assert window, "GitHub window did not open"
    xdo("windowsize", window, 1100, 780)
    time.sleep(0.8)
    assert state("organizations")["rows"] == 1
    shot("organizations.png")
    action("kryonlabs")
    assert state("repositories")["organization"] == "kryonlabs"
    shot("organization-repositories.png")
    # Repository lists include a search bar above their rows.
    action("kryonlabs/example")
    assert state("overview")["repo"] == "kryonlabs/example"
    shot("repository-overview.png")
    action("Code")
    assert state("code")["rows"] == 30
    shot("repository-code.png")
    # Exercise scrolling and back navigation, not merely drawing four frames.
    xdo("mousemove", "--window", window, 700, 500)
    xdo("click", "--repeat", "4", "--delay", "50", "5")
    time.sleep(0.5)
    assert state()["scroll"] > 0
    action("Back")
    state("overview")
    action("Issues")
    action("New issue")
    time.sleep(0.3)
    assert state()["composer"] == 1
    action("Close")
    assert state()["composer"] == 0
    xdo("windowsize", window, 560, 820)
    time.sleep(0.5)
    shot("narrow.png")
    assert process.poll() is None, "GitHub client exited during navigation"
    print("Native organizations, repositories, overview, code scrolling, back navigation, closable issue editor and narrow layout verified")
finally:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
