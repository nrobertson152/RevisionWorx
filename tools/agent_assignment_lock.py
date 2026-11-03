#!/usr/bin/env python3
"""Atomic GitHub assignment checkout and explicit check-in. Python stdlib only."""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROLES = {"core", "gui", "qa", "distributed", "integration", "bom", "change-control"}

class LockError(RuntimeError):
    pass

def api(method, path, payload=None):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise LockError("GITHUB_TOKEN or GH_TOKEN required; no lock acquired")
    repo = os.environ.get("GITHUB_REPOSITORY", "nrobertson152/RevisionWorx")
    base = "https://api.github.com/repos/" + repo
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
        "User-Agent": "RevisionWorx-agent-assignment-lock/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            body = res.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode(errors="replace")[:300]
        raise LockError(f"GitHub HTTP {exc.code} {method} {path}: {details}") from exc

def lock_ref(role):
    if role not in ROLES:
        raise LockError(f"Invalid role: {role}")
    return "refs/heads/agent-locks/" + role

def current(role):
    path = "/git/ref/heads/agent-locks/" + role
    try:
        return api("GET", path)["object"]["sha"]
    except LockError as exc:
        if "HTTP 404 " in str(exc):
            return None
        raise

def checkout(role, owner, task):
    if not owner.strip() or not task.strip():
        raise LockError("owner and task are mandatory")
    # A unique commit SHA acts as the receipt; never mutate the working code branch.
    base = api("GET", "/git/ref/heads/main")["object"]["sha"]
    tree = api("GET", "/git/commits/" + base)["tree"]["sha"]
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    nonce = str(uuid.uuid4())
    commit = api("POST", "/git/commits", {
        "message": f"assignment-lock role={role} owner={owner} task={task} session={nonce} utc={stamp}",
        "tree": tree, "parents": [base]})["sha"]
    try:
        api("POST", "/git/refs", {"ref": lock_ref(role), "sha": commit})
    except LockError as exc:
        raise LockError(f"LOCK BUSY or acquisition failed: {lock_ref(role)}; {exc}") from exc
    return {"status": "CHECKED_OUT", "role": role, "owner": owner,
            "task": task, "sha": commit, "ref": lock_ref(role), "utc": stamp}

def verify(role, sha):
    found = current(role)
    if not found or found != sha:
        raise LockError(f"LOCK NOT OWNED: {lock_ref(role)} expected={sha} actual={found}")
    return {"status": "OWNED", "role": role, "sha": sha}

def checkin(role, sha):
    verify(role, sha)
    # GitHub REST DELETE has no compare-and-swap. No agent may steal or mutate a held ref.
    api("DELETE", "/git/refs/heads/agent-locks/" + role)
    if current(role) is not None:
        raise LockError("Check-in not confirmed: ref still exists")
    return {"status": "CHECKED_IN", "role": role, "sha": sha}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    for name in ("checkout", "verify", "checkin", "status"):
        s = sub.add_parser(name)
        s.add_argument("--role", choices=sorted(ROLES), required=True)
        if name in ("verify", "checkin"):
            s.add_argument("--sha", required=True)
        if name == "checkout":
            s.add_argument("--owner", required=True)
            s.add_argument("--task", required=True)
    args = p.parse_args()
    try:
        if args.action == "checkout":
            result = checkout(args.role, args.owner, args.task)
        elif args.action == "verify":
            result = verify(args.role, args.sha)
        elif args.action == "checkin":
            result = checkin(args.role, args.sha)
        else:
            result = {"role": args.role, "sha": current(args.role), "status": "BUSY" if current(args.role) else "FREE"}
        print(json.dumps(result, sort_keys=True))
    except LockError as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}), file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
