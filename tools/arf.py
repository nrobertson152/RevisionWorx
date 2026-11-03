#!/usr/bin/env python3
"""ARF workflow tool. Standard library only; talks to GitHub through the `gh` CLI.

Trust model: a model never runs in a job that holds ARF_GH_TOKEN. Every workflow is
split into a prepare job (no model, read token), a model job (no write token; its
output is an artifact), and a publish job on a fresh runner that runs this tool from
the default branch, treats the artifact as untrusted data, validates it and only
then writes to GitHub. Commit statuses that gate merging are written with the
workflow's own GITHUB_TOKEN, so the ruleset can require them from GitHub Actions and
a status forged with the Director's token does not count.

Subcommands
  labels             create or update the ARF labels
  snapshot           coordinator: write open issues/PRs for the model; decide if a run is needed
  apply-plan         coordinator: validate plan.json against a fresh snapshot and apply it
  prepare-implement  implement: choose branch/model/base commits, write the task file
  package-work       implement (model job): turn the working tree into a patch artifact
  publish-implement  implement: validate patch + whole branch + PR text, push, record provenance, open/update PR
  prepare-review     review: static gate, choose a different model and depth, write context, set pending
  publish-verdict    review: validate the verdict against the contract and risk, then record it
  check-pr           policy check for the PR description, changed paths and rule approvals
  director-approval  record a Director-only decision (rules change, or provenance of a hand-pushed commit)
  report             comment on an issue/PR and adjust labels
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(".arf-out")

REVIEW_CONTEXT = "arf/cross-model-review"
RULES_CONTEXT = "arf/rules-approval"
PROVENANCE_CONTEXT = "arf/provenance"
PROVENANCE_RE = re.compile(r"^implementer=([A-Za-z0-9._:/-]{1,80})(?: |$)")
ACTIONS_BOT = "github-actions[bot]"
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
RISKS = ("low", "medium", "high", "critical")
PROTECTED = ("AGENTS.md", "CLAUDE.md", ".agents/", ".github/", "tools/arf.py", "tools/arf_tests/")
REQUIRED_SECTIONS = ("Summary", "Issue", "Scope", "Verification", "Risks", "Handoff")
FLOW_LABELS = ("agent:triage", "agent:implement", "agent:in-pr", "agent:review", "agent:rework",
               "ready-to-merge", "needs:director", "blocked")
DEPTH = {"low": "light", "medium": "standard", "high": "full", "critical": "full"}
MAX_TURNS = {"light": 25, "standard": 60, "full": 120}
MAX_PATCH_BYTES = 20 * 1024 * 1024

LABELS = {
    "agent:triage": ("ededed", "New issue waiting for the coordinator"),
    "agent:implement": ("1d76db", "Dispatched to an implementer"),
    "agent:in-pr": ("0e8a16", "Has an open agent PR"),
    "agent:review": ("5319e7", "Request a cross-model (re-)review"),
    "agent:rework": ("d93f0b", "Reviewer rejected; implementer is fixing"),
    "ready-to-merge": ("0e8a16", "Cross-model ACCEPT at the current head"),
    "needs:director": ("b60205", "A human decision is required"),
    "blocked": ("000000", "Waiting on another issue or PR"),
    "risk:low": ("c2e0c6", "Impact if wrong: low"),
    "risk:medium": ("fbca04", "Impact if wrong: medium"),
    "risk:high": ("e99695", "Impact if wrong: high"),
    "risk:critical": ("b60205", "Impact if wrong: critical"),
    "rules-change": ("5319e7", "May change protected files; merge also needs the Director's approval run"),
    "arf:briefing": ("c5def5", "The Director briefing issue"),
    "type:bug": ("d73a4a", "Something is wrong"),
}

CONTRACT_RE = re.compile(r"<!-- arf:contract (\{.*?\}) -->\n?(.*?)<!-- /arf:contract -->\n?", re.S)
VERDICT_RE = re.compile(r"<!-- arf:verdict (\{.*?\}) -->")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
HEADING_RE = re.compile(r"(?m)^##[ \t]+(.+?)[ \t]*\r?$")
AC_RE = re.compile(r"(?m)^\s*[-*]\s*\[[ xX]\]\s*(AC\d+)\s*[:.)-]\s*(.+?)\s*$")
ISSUE_REF_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)\s*:?\s+"
    r"(?:#(\d+)|https://github\.com/[^\s/]+/[^\s/]+/issues/(\d+))")
MARKER_RE = {
    "rules": re.compile(r"(?m)^ARF-Rules:[ \t]*(\S*)[ \t]*\r?$"),
    "model": re.compile(r"(?m)^Implemented-by-model:[ \t]*(\S*)[ \t]*\r?$"),
}
DIFF_HEADER_RE = re.compile(r'^diff --git "?a/(.+?)"? "?b/(.+?)"?$')
MODE_RE = re.compile(r"^(?:new file mode|new mode|old mode|deleted file mode|index [0-9a-f]+\.\.[0-9a-f]+) ?(\d{6})?$")


# ---------------------------------------------------------------- configuration

def rules_version(base: Path = ROOT) -> str:
    return (base / ".agents" / "VERSION").read_text(encoding="utf-8").strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def config() -> dict:
    env = os.environ.get
    return {
        "max_parallel": env_int("ARF_MAX_PARALLEL", 2),
        "max_review_rounds": env_int("ARF_MAX_REVIEW_ROUNDS", 3),
        "auto_merge": (env("ARF_AUTO_MERGE") or "none").strip().lower(),
        "implementer": (env("ARF_IMPLEMENTER") or "claude").strip().lower(),
        "reviewer": (env("ARF_REVIEWER") or "auto").strip().lower(),
        "models": {"claude": env("ARF_CLAUDE_MODEL") or "claude-opus-5-5",
                   "codex": env("ARF_CODEX_MODEL") or "codex-default"},
        "review_models": {"claude": env("ARF_CLAUDE_REVIEW_MODEL") or "",
                          "codex": env("ARF_CODEX_REVIEW_MODEL") or ""},
        "light_models": {"claude": env("ARF_CLAUDE_LIGHT_MODEL") or "",
                         "codex": env("ARF_CODEX_LIGHT_MODEL") or ""},
    }


class ArfError(Exception):
    pass


# ---------------------------------------------------------------- paths and contracts

def normalize_path(p: str) -> str:
    p = p.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    parts = p.split("/")
    if not p or p.startswith("/") or ".." in parts or ".git" in parts:
        raise ArfError(f"invalid path: {p!r}")
    if ".arf-out" in parts:
        raise ArfError(f"{p}: .arf-out/ is reserved for agent output and may not be committed")
    return p


def path_covers(allowed: str, path: str) -> bool:
    a = allowed.rstrip("/")
    return path == a or path.startswith(a + "/")


def paths_overlap(a: list[str], b: list[str]) -> bool:
    return any(path_covers(x, y) or path_covers(y, x) for x in a for y in b)


def is_protected(path: str) -> bool:
    return any(path == p.rstrip("/") or (p.endswith("/") and path.startswith(p)) for p in PROTECTED)


def check_changed_paths(changed: list[str], allowed: list[str] | None, rules_change: bool) -> list[str]:
    """Violations. allowed=None means no contract restriction (policy check)."""
    errors = []
    for path in changed:
        if is_protected(path) and not rules_change:
            errors.append(f"{path}: protected rule file; needs an issue labelled rules-change")
        elif allowed is not None and not any(path_covers(a, path) for a in allowed):
            errors.append(f"{path}: outside the contract's allowed paths")
    return errors


def parse_contract(body: str | None) -> dict | None:
    m = CONTRACT_RE.search(body or "")
    if not m:
        return None
    try:
        meta = json.loads(m.group(1))
        paths = [normalize_path(p) for p in meta.get("paths", [])]
    except (json.JSONDecodeError, ArfError):
        return None
    text = m.group(2).strip()
    return {"paths": paths, "risk": meta.get("risk"), "text": text, "criteria": acceptance_criteria(text),
            "duplicate_ids": duplicate_criteria(text)}


def acceptance_criteria(text: str) -> dict[str, str]:
    return {m.group(1).upper(): m.group(2) for m in AC_RE.finditer(text or "")}


def duplicate_criteria(text: str) -> list[str]:
    ids = [m.group(1).upper() for m in AC_RE.finditer(text or "")]
    return sorted({i for i in ids if ids.count(i) > 1})


def contract_digest(contract: dict | None) -> str:
    if not contract:
        return "none"
    canon = json.dumps({"text": contract["text"], "paths": contract["paths"], "risk": contract.get("risk")},
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def render_contract(text: str, paths: list[str], risk: str) -> str:
    meta = json.dumps({"paths": paths, "risk": risk}, separators=(",", ":"))
    return f"<!-- arf:contract {meta} -->\n### ARF contract\n\n{text.strip()}\n<!-- /arf:contract -->\n"


def upsert_contract(body: str | None, block: str) -> str:
    body = body or ""
    if CONTRACT_RE.search(body):
        return CONTRACT_RE.sub(lambda _m: block, body, count=1)
    return body.rstrip() + "\n\n" + block


# ---------------------------------------------------------------- PR text

def sections(body: str) -> dict[str, str]:
    body = COMMENT_RE.sub("", body or "")
    heads = list(HEADING_RE.finditer(body))
    out = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        words = m.group(1).strip().split()
        out[words[0].strip(":").lower() if words else ""] = body[m.end():end].strip()
    return out


def marker(body: str, key: str) -> str:
    m = MARKER_RE[key].search(COMMENT_RE.sub("", body or ""))
    return m.group(1) if m else ""


def set_marker(body: str, key: str, value: str) -> str:
    label = {"rules": "ARF-Rules", "model": "Implemented-by-model"}[key]
    line = f"{label}: {value}"
    if MARKER_RE[key].search(body):
        return MARKER_RE[key].sub(lambda _m: line, body, count=1)
    return body.rstrip() + "\n" + line + "\n"


def linked_issue(body: str) -> int | None:
    m = ISSUE_REF_RE.search(COMMENT_RE.sub("", body or ""))
    return int(m.group(1) or m.group(2)) if m else None


def validate_pr_body(body: str, version: str, draft: bool = False) -> list[str]:
    if draft:
        return []
    errors = []
    found = sections(body)
    for name in REQUIRED_SECTIONS:
        content = MARKER_RE["rules"].sub("", MARKER_RE["model"].sub("", found.get(name.lower(), ""))).strip()
        if not content:
            errors.append(f"Missing or empty section: ## {name}")
    if found.get("issue") and linked_issue(found["issue"]) is None:
        errors.append("## Issue must say 'Closes #<n>' (or Fixes/Resolves/Refs) with a real issue")
    got = marker(body, "rules")
    if got != version:
        errors.append(f"ARF-Rules must be {version} (found {got or 'nothing'}); re-read AGENTS.md")
    if not marker(body, "model"):
        errors.append("Implemented-by-model must name the exact model id, or 'human'")
    return errors


# ---------------------------------------------------------------- models and review depth

def engine_of(model: str) -> str | None:
    m = (model or "").lower()
    if not m or m == "human":
        return None
    return "claude" if m.startswith("claude") else "codex"


def pick_reviewer(implementer_model: str, cfg: dict, depth: str = "standard") -> tuple[str, str]:
    """Reviewer engine and model, always a different model from the implementer's."""
    impl_engine = engine_of(implementer_model)
    if cfg["reviewer"] in ("claude", "codex"):
        engine = cfg["reviewer"]
    elif impl_engine:
        engine = "codex" if impl_engine == "claude" else "claude"
    else:
        engine = "codex" if cfg["implementer"] == "claude" else "claude"
    model = ((depth == "light" and cfg["light_models"][engine])
             or cfg["review_models"][engine] or cfg["models"][engine])
    if model.lower() == (implementer_model or "").lower():
        raise ArfError(f"reviewer model {model} is the same as the implementer's; "
                       f"set ARF_{engine.upper()}_REVIEW_MODEL to a different model")
    return engine, model


def load_floors(base: Path = ROOT) -> dict[str, str]:
    path = base / ".agents" / "risk-floors.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    floors = {normalize_path(k): v for k, v in (data.get("floors") or {}).items()}
    bad = {k: v for k, v in floors.items() if v not in RISKS}
    if bad:
        raise ArfError(f"risk-floors.json has invalid levels: {bad}")
    return floors


def floor_for(paths: list[str], floors: dict[str, str]) -> str | None:
    """Highest floor that applies to any of the paths (either side may be a directory)."""
    hits = [lvl for fp, lvl in floors.items() if any(path_covers(fp, p) or path_covers(p, fp) for p in paths)]
    return max(hits, key=RISKS.index) if hits else None


def risk_of(labels: set[str]) -> str | None:
    for r in reversed(RISKS):
        if f"risk:{r}" in labels:
            return r
    return None


def max_risk(*risks: str | None) -> str:
    known = [r for r in risks if r in RISKS]
    return max(known, key=RISKS.index) if known else "medium"


def auto_merge_allowed(setting: str, risk: str | None) -> bool:
    if setting not in RISKS[:2] or risk not in RISKS[:2]:
        return False
    return RISKS.index(risk) <= RISKS.index(setting)


# ---------------------------------------------------------------- verdicts

def validate_verdict(v: dict, version: str, criteria: dict[str, str] | None = None,
                     risk: str = "medium") -> list[str]:
    """A verdict is evidence to check, not a decision to obey."""
    errors = []
    if v.get("rules_version") != version:
        errors.append(f"rules_version must be {version}")
    result = v.get("result")
    if result not in ("ACCEPT", "REJECT"):
        errors.append("result must be ACCEPT or REJECT")
    if not isinstance(v.get("summary"), str) or not v["summary"].strip():
        errors.append("summary is required")
    crit = v.get("criteria")
    if not isinstance(crit, list) or not crit or not all(isinstance(c, dict) for c in crit):
        return errors + ["criteria must list every acceptance criterion as objects"]
    for c in crit:
        if c.get("result") not in ("PASS", "FAIL", "NOT RUN"):
            errors.append(f"criterion {c.get('id', '?')}: result must be PASS, FAIL or NOT RUN")
        if not str(c.get("evidence") or "").strip():
            errors.append(f"criterion {c.get('id', '?')}: evidence is required")
    findings = v.get("findings") or []
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        findings = []
    tests_run = v.get("tests_run") or []
    not_verified = " ".join(str(x) for x in (v.get("not_verified") or []))
    criteria = criteria or {}
    ids = [str(c.get("id", "")).strip().upper() for c in crit]
    by_id = dict(zip(ids, crit))
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        errors.append(f"criteria listed more than once: {', '.join(dupes)}")
    missing = [i for i in criteria if i not in by_id]
    if missing:
        errors.append(f"verdict does not address contract criteria {', '.join(missing)}")
    if criteria:
        unexpected = [i or "(no id)" for i in ids if i not in criteria]
        if unexpected:
            errors.append(f"criteria not in the contract: {', '.join(unexpected)}")
        for i, c in by_id.items():
            claimed = c.get("criterion")
            if i in criteria and claimed is not None and _norm(claimed) != _norm(criteria[i]):
                errors.append(f"{i}: description does not match the contract's {i}; report by id only")
    if result == "ACCEPT":
        if any(c.get("result") == "FAIL" for c in crit):
            errors.append("ACCEPT with a FAIL criterion")
        if any(isinstance(f, dict) and f.get("severity") in ("high", "critical") for f in findings):
            errors.append("ACCEPT with a high/critical finding")
        not_run = [str(c.get("id") or c.get("criterion")) for c in crit if c.get("result") == "NOT RUN"]
        if risk in ("high", "critical"):
            if not criteria:
                errors.append(f"{risk}-risk ACCEPT needs a contract with numbered acceptance criteria")
            if not_run:
                errors.append(f"{risk}-risk ACCEPT with criteria not run: {', '.join(not_run)}")
        else:
            unexplained = [i for i in not_run if i not in not_verified]
            if unexplained:
                errors.append(f"criteria NOT RUN without a not_verified reason: {', '.join(unexplained)}")
        if risk != "low" and not tests_run:
            errors.append(f"{risk}-risk ACCEPT with no tests_run")
    return errors


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def render_verdict(v: dict, meta: dict, criteria: dict[str, str] | None = None) -> str:
    """The criterion text shown is always the contract's, never the reviewer's."""
    cell = lambda s: str(s or "").replace("|", "\\|").replace("\n", " ")
    criteria = criteria or {}
    lines = [f"<!-- arf:verdict {json.dumps(meta, separators=(',', ':'))} -->",
             f"## Cross-model review: **{v['result']}**",
             f"Reviewer `{meta['model']}` ({meta.get('depth', 'standard')} review) at `{meta['sha'][:12]}`, "
             f"round {meta['round']}. Implementer `{meta['implementer']}`. Risk: {meta.get('risk', '?')}.",
             "", v["summary"].strip(), "", "| Id | Criterion | Result | Evidence |", "| --- | --- | --- | --- |"]
    for c in v.get("criteria", []):
        cid = str(c.get("id", "")).strip().upper()
        text = criteria.get(cid, "(not in the contract)" if criteria else c.get("criterion"))
        evidence = cell(c.get("evidence")) + (f" — limits: {cell(c['limitations'])}" if c.get("limitations") else "")
        lines.append(f"| {cell(cid)} | {cell(text)} | {c.get('result')} | {evidence} |")
    if v.get("findings"):
        lines += ["", "**Findings**"]
        for f in v["findings"]:
            where = f"`{f.get('file')}:{f.get('line')}`" if f.get("file") else ""
            lines.append(f"- **{f.get('severity', '?')}** {where} {f.get('problem', '')}"
                         + (f" — fix: {f['fix']}" if f.get("fix") else ""))
    for key, title in (("tests_run", "Tests run"), ("not_verified", "Not verified")):
        if v.get(key):
            lines += ["", f"**{title}**"] + [f"- {t}" for t in v[key]]
    if v.get("notes"):
        lines += ["", f"_Notes:_ {v['notes']}"]
    return "\n".join(lines) + "\n"


def latest_verdict(comments: list[dict]) -> tuple[dict | None, str | None]:
    for c in reversed(comments):
        m = VERDICT_RE.search(c.get("body") or "")
        if m and c.get("author_association") in TRUSTED_ASSOCIATIONS:
            try:
                return json.loads(m.group(1)), c["body"]
            except json.JSONDecodeError:
                continue
    return None, None


def count_verdicts(comments: list[dict]) -> int:
    """Review rounds so far. Only verdict comments by trusted accounts count (ARF posts them as the Director),
    so outsiders can't exhaust the round limit by pasting the marker."""
    return sum(1 for c in comments if c.get("author_association") in TRUSTED_ASSOCIATIONS
               and VERDICT_RE.search(c.get("body") or ""))


# ---------------------------------------------------------------- patches (untrusted artifact)

def inspect_patch(patch: str) -> tuple[list[str], list[str]]:
    """Return (paths touched, errors) from a `git diff --binary` patch, without applying it."""
    paths, errors = set(), []
    if len(patch.encode("utf-8", "surrogateescape")) > MAX_PATCH_BYTES:
        return [], [f"patch larger than {MAX_PATCH_BYTES // (1024 * 1024)} MB"]
    for line in patch.splitlines():
        m = DIFF_HEADER_RE.match(line)
        if m:
            for p in m.groups():
                try:
                    paths.add(normalize_path(p))
                except ArfError as e:
                    errors.append(str(e))
            continue
        if line.startswith(("new file mode ", "new mode ", "old mode ", "deleted file mode ", "index ")):
            mode = line.rsplit(" ", 1)[-1]
            if mode in ("120000", "160000"):
                errors.append("symlinks and submodules are not accepted from agents")
        if line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            try:
                paths.add(normalize_path(line.split(" ", 2)[2]))
            except ArfError as e:
                errors.append(str(e))
    return sorted(paths), sorted(set(errors))


# ---------------------------------------------------------------- plan validation

def label_names(obj: dict) -> set[str]:
    return {l["name"] if isinstance(l, dict) else l for l in obj.get("labels", [])}


def validate_plan(plan: dict, state: dict, version: str, cfg: dict,
                  floors: dict[str, str] | None = None) -> tuple[list[dict], list[str]]:
    """(accepted actions, refusals). Pure. Risk is raised to path floors and never lowered."""
    floors = floors or {}
    if not isinstance(plan, dict) or plan.get("rules_version") != version:
        got = plan.get("rules_version") if isinstance(plan, dict) else None
        return [], [f"Plan rejected: rules_version is {got!r}, expected {version}"]
    issues = {i["number"]: i for i in state.get("issues", [])}
    in_flight = {n: (parse_contract(i.get("body")) or {}).get("paths", [])
                 for n, i in issues.items() if label_names(i) & {"agent:implement", "agent:in-pr"}}
    accepted, rejected, new_dispatches = [], [], 0
    for a in plan.get("actions", []) or []:
        if not isinstance(a, dict):
            rejected.append("non-object action ignored")
            continue
        n = a.get("issue")
        issue = issues.get(n)
        why = None
        if issue is None:
            why = "not an open issue"
        elif issue.get("author_association") not in TRUSTED_ASSOCIATIONS:
            why = "author is not the Director or a collaborator"
        elif "arf:briefing" in label_names(issue):
            why = "is the briefing issue"
        elif a.get("risk") not in RISKS:
            why = f"risk must be one of {', '.join(RISKS)}"
        if why:
            rejected.append(f"#{n}: {why}")
            continue
        labels = label_names(issue)
        try:
            paths = [normalize_path(p) for p in (a.get("paths") or [])]
        except ArfError as e:
            rejected.append(f"#{n}: {e}")
            continue
        existing = parse_contract(issue.get("body"))
        required = max_risk(a["risk"], risk_of(labels), existing and existing.get("risk"),
                            floor_for(paths or (existing or {}).get("paths", []), floors))
        if required != a["risk"]:
            rejected.append(f"#{n}: risk raised from {a['risk']} to {required} (path floor or existing risk; "
                            "only the Director lowers risk)")
            a = {**a, "risk": required}
        if a.get("contract") and duplicate_criteria(a["contract"]):
            rejected.append(f"#{n}: contract repeats criterion ids {duplicate_criteria(a['contract'])}; not applied")
            a = {**a, "contract": None, "dispatch": False}
        if a.get("contract") and not acceptance_criteria(a["contract"]):
            rejected.append(f"#{n}: contract has no numbered criteria (- [ ] AC1: ...); not applied")
            a = {**a, "contract": None, "dispatch": False}
        if a.get("dispatch"):
            if not (a.get("contract") or existing):
                why = "dispatch needs a contract"
            elif not a.get("contract") and existing.get("duplicate_ids"):
                why = f"existing contract repeats criterion ids {existing['duplicate_ids']}"
            elif not paths and not (existing and existing["paths"]):
                why = "dispatch needs allowed paths"
            elif labels & {"blocked", "needs:director", "agent:in-pr"}:
                why = "is blocked, waiting on the Director, or already has a PR"
            elif a.get("needs_director"):
                why = "cannot both dispatch and wait for the Director"
            if not why:
                paths = paths or existing["paths"]
                bad = [p for p in paths if is_protected(p)]
                if bad and "rules-change" not in labels:
                    why = f"paths include protected files {bad} without rules-change"
            if not why:
                clash = [m for m, p in in_flight.items() if m != n and paths_overlap(paths, p)]
                if clash:
                    why = "allowed paths overlap in-flight " + ", ".join(f"#{m}" for m in clash)
            redispatch = "agent:implement" in labels
            if not why and not redispatch and len(in_flight) + new_dispatches >= cfg["max_parallel"]:
                why = f"would exceed max_parallel={cfg['max_parallel']}"
            if why:
                rejected.append(f"#{n}: not dispatched — {why}")
                a = {**a, "dispatch": False}
            else:
                new_dispatches += 0 if redispatch else 1
                in_flight[n] = paths
        if a.get("needs_director") and not (a.get("comment") or "").strip():
            rejected.append(f"#{n}: needs_director without a comment stating the decision")
            a = {**a, "needs_director": False}
        accepted.append({**a, "paths": paths})
    return accepted, rejected


def needs_coordination(state: dict, event_name: str, stale_hours: float) -> str:
    """Reason to run the coordinator model, or '' to skip a quiet scheduled run."""
    if event_name != "schedule":
        return f"event {event_name}"
    issues = [i for i in state["issues"] if i.get("author_association") in TRUSTED_ASSOCIATIONS
              and "arf:briefing" not in i["labels"]]
    if any("agent:triage" in i["labels"] for i in issues):
        return "untriaged issues"
    if any(set(i["labels"]) & {"agent:implement", "agent:in-pr", "agent:rework"}
           and (i.get("idle_hours") or 0) > stale_hours for i in [*issues, *state["pull_requests"]]):
        return "stale in-flight work"
    in_flight = sum(1 for i in issues if set(i["labels"]) & {"agent:implement", "agent:in-pr"})
    held = [i for i in issues if not set(i["labels"]) & set(FLOW_LABELS)]
    if held and in_flight < state["limits"]["max_parallel"]:
        return "capacity for held issues"
    return ""


def trusted_statuses(statuses: list[dict], context: str) -> list[dict]:
    """Statuses for a context written by GitHub Actions (GITHUB_TOKEN), newest first."""
    mine = [s for s in statuses if s.get("context") == context
            and (s.get("creator") or {}).get("login") == ACTIONS_BOT]
    return sorted(mine, key=lambda s: s.get("updated_at") or s.get("created_at") or "", reverse=True)


def trusted_implementer(statuses: list[dict]) -> str:
    """The implementer recorded by ARF for this exact commit, or '' if there is no trusted record."""
    for s in trusted_statuses(statuses, PROVENANCE_CONTEXT):
        m = PROVENANCE_RE.match(s.get("description") or "")
        if s.get("state") == "success" and m:
            return m.group(1)
    return ""


def rules_approval_ok(statuses: list[dict], sha: str) -> bool:
    """True if the latest arf/rules-approval status on sha is success and was set by GitHub Actions."""
    mine = [s for s in statuses if s.get("context") == RULES_CONTEXT]
    if not mine:
        return False
    latest = max(mine, key=lambda s: s.get("updated_at") or s.get("created_at") or "")
    return latest.get("state") == "success" and (latest.get("creator") or {}).get("login") == ACTIONS_BOT


def check_pr(event: dict, base_version: str, changed: list[str], statuses: list[dict] | None = None,
             require_rules_approval: bool = True) -> list[str]:
    pr = event["pull_request"]
    errors = validate_pr_body(pr.get("body") or "", base_version, bool(pr.get("draft")))
    labels = label_names(pr)
    errors += check_changed_paths(changed, None, "rules-change" in labels)
    if require_rules_approval and any(is_protected(p) for p in changed) and "rules-change" in labels \
            and not rules_approval_ok(statuses or [], pr["head"]["sha"]):
        errors.append("protected files changed: run the 'ARF Director approval' workflow (kind: rules) for this PR's "
                      "head commit (only the Director can)")
    return errors


# ---------------------------------------------------------------- GitHub access

class GitHub:
    """`gh` wrapper. GH_TOKEN is the Director's token in publish jobs, GITHUB_TOKEN elsewhere.
    ARF_STATUS_TOKEN (the workflow's GITHUB_TOKEN) is used for gate statuses."""

    def __init__(self, repo: str | None = None, runner=None):
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
        self._run = runner or self._default_run

    @staticmethod
    def _default_run(args: list[str], stdin: str | None = None, token: str | None = None) -> str:
        env = dict(os.environ)
        if token:
            env["GH_TOKEN"] = token
        r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise ArfError(f"gh {' '.join(args[:3])} failed: {r.stderr.strip()[:500]}")
        return r.stdout

    def api(self, path: str, method: str = "GET", data: dict | None = None, paginate: bool = False,
            token: str | None = None):
        args = ["api", "-X", method, path.replace("{repo}", self.repo), "-H", "Accept: application/vnd.github+json"]
        if paginate:
            args.append("--paginate")
        stdin = json.dumps(data) if data is not None else None
        if stdin is not None:
            args += ["--input", "-"]
        out = self._run(args, stdin, token) if token else self._run(args, stdin)
        if paginate:
            out = out.replace("][", ",")
        return json.loads(out) if out.strip() else None

    def issue(self, n):
        return self.api(f"repos/{{repo}}/issues/{n}")

    def comments(self, n):
        return self.api(f"repos/{{repo}}/issues/{n}/comments?per_page=100", paginate=True) or []

    def comment(self, n, body):
        self.api(f"repos/{{repo}}/issues/{n}/comments", "POST", {"body": body})

    def edit_issue(self, n, **fields):
        self.api(f"repos/{{repo}}/issues/{n}", "PATCH", fields)

    def add_labels(self, n, labels):
        if labels:
            self.api(f"repos/{{repo}}/issues/{n}/labels", "POST", {"labels": sorted(labels)})

    def remove_label(self, n, label):
        try:
            self.api(f"repos/{{repo}}/issues/{n}/labels/{label.replace(':', '%3A')}", "DELETE")
        except ArfError:
            pass

    def relabel(self, n, add=(), remove=()):
        for l in remove:
            self.remove_label(n, l)
        self.add_labels(n, set(add))

    def set_status(self, sha, state, description, context=REVIEW_CONTEXT):
        token = os.environ.get("ARF_STATUS_TOKEN")
        if not token:
            raise ArfError("ARF_STATUS_TOKEN (the workflow's GITHUB_TOKEN) is required to set gate statuses")
        data = {"state": state, "context": context, "description": description[:140]}
        if run_url():
            data["target_url"] = run_url()
        self.api(f"repos/{{repo}}/statuses/{sha}", "POST", data, token=token)

    def statuses(self, sha):
        return self.api(f"repos/{{repo}}/commits/{sha}/statuses?per_page=100", paginate=True) or []

    def pr(self, n):
        return self.api(f"repos/{{repo}}/pulls/{n}")


def head_repo(pr: dict) -> str:
    return ((pr.get("head") or {}).get("repo") or {}).get("full_name") or ""


def run_url() -> str | None:
    s, r, i = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{s}/{r}/actions/runs/{i}" if s and r and i else None


def set_outputs(**values):
    lines = []
    for k, v in values.items():
        v = "" if v is None else str(v)
        if "\n" in v:
            raise ArfError(f"output {k} contains a newline")
        lines.append(f"{k}={v}")
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


SAFE_GIT = ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false"]


def git(*args, cwd=".", check=True) -> str:
    r = subprocess.run(["git", *SAFE_GIT, *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise ArfError(f"git {' '.join(args[:2])} failed: {r.stderr.strip()[:500]}")
    return r.stdout


def trusted_comments(comments: list[dict], limit: int = 20) -> list[dict]:
    return [c for c in comments if c.get("author_association") in TRUSTED_ASSOCIATIONS][-limit:]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-") or "task"


# ---------------------------------------------------------------- coordinator

def cmd_labels(gh: GitHub, _args):
    existing = {l["name"] for l in gh.api("repos/{repo}/labels?per_page=100", paginate=True) or []}
    for name, (color, desc) in LABELS.items():
        data = {"color": color, "description": desc}
        if name in existing:
            gh.api(f"repos/{{repo}}/labels/{name.replace(':', '%3A')}", "PATCH", data)
        else:
            gh.api("repos/{repo}/labels", "POST", {"name": name, **data})
    print(f"{len(LABELS)} labels ensured")


def build_snapshot(gh: GitHub) -> dict:
    now = datetime.now(timezone.utc)
    issues, prs = [], []
    for it in gh.api("repos/{repo}/issues?state=open&per_page=100", paginate=True) or []:
        entry = {"number": it["number"], "title": it["title"], "labels": sorted(label_names(it)),
                 "author_association": it.get("author_association"), "updated_at": it.get("updated_at"),
                 "idle_hours": round((now - datetime.fromisoformat(it["updated_at"].replace("Z", "+00:00"))
                                      ).total_seconds() / 3600, 1) if it.get("updated_at") else None}
        is_pr = "pull_request" in it
        if it.get("author_association") not in TRUSTED_ASSOCIATIONS:
            entry["body"] = "(hidden: author is not a collaborator)"
            (prs if is_pr else issues).append(entry)
            continue
        entry["body"] = it.get("body") or ""
        entry["comments"] = [{"author_association": c["author_association"], "created_at": c["created_at"],
                              "body": c["body"][:3000]} for c in trusted_comments(gh.comments(it["number"]), 8)]
        if is_pr:
            pr = gh.pr(it["number"])
            review = next((s for s in gh.statuses(pr["head"]["sha"]) if s["context"] == REVIEW_CONTEXT), None)
            changed = [f["filename"] for f in
                       gh.api(f"repos/{{repo}}/pulls/{it['number']}/files?per_page=100", paginate=True) or []]
            entry["changes_workflows"] = any(f.startswith(".github/workflows/") for f in changed)
            entry.update({"head_ref": pr["head"]["ref"], "head_sha": pr["head"]["sha"], "draft": pr.get("draft"),
                          "linked_issue": linked_issue(pr.get("body") or ""),
                          "implemented_by": marker(pr.get("body") or "", "model"),
                          "cross_model_review": review and {"state": review["state"],
                                                            "description": review["description"]}})
            prs.append(entry)
        else:
            issues.append(entry)
    cfg = config()
    return {"generated_at": now.isoformat(), "rules_version": rules_version(),
            "limits": {k: cfg[k] for k in ("max_parallel", "max_review_rounds", "auto_merge")},
            "issues": issues, "pull_requests": prs}


def cmd_snapshot(gh: GitHub, args):
    OUT.mkdir(exist_ok=True)
    state = build_snapshot(gh)
    Path(args.out).write_text(json.dumps(state, indent=1), encoding="utf-8")
    reason = needs_coordination(state, os.environ.get("GITHUB_EVENT_NAME", ""),
                                float(os.environ.get("ARF_STALE_HOURS") or 24))
    set_outputs(run="true" if reason else "", reason=reason or "nothing to do")


def upsert_briefing(gh: GitHub, state: dict, text: str):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = (f"_Updated by the coordinator {stamp}. Comment on the linked issues, not here._\n\n"
            f"{text.strip()[:60000]}\n")
    existing = [i for i in state.get("issues", []) if "arf:briefing" in i.get("labels", [])
                and i.get("author_association") in TRUSTED_ASSOCIATIONS]
    if existing:
        gh.edit_issue(existing[0]["number"], body=body)
    else:
        gh.api("repos/{repo}/issues", "POST", {"title": "ARF: Director briefing", "body": body,
                                               "labels": ["arf:briefing"]})


def apply_plan(gh: GitHub, plan, state: dict, version: str, cfg: dict) -> list[str]:
    actions, rejected = validate_plan(plan, state, version, cfg, load_floors())
    issues = {i["number"]: i for i in state.get("issues", [])}
    for a in actions:
        n, issue = a["issue"], issues[a["issue"]]
        labels = set(issue.get("labels", []))
        if a.get("contract"):
            gh.edit_issue(n, body=upsert_contract(issue.get("body"),
                                                  render_contract(a["contract"], a["paths"], a["risk"])))
        add = {f"risk:{a['risk']}"} | ({"needs:director"} if a.get("needs_director") else set())
        remove = ({f"risk:{r}" for r in RISKS if r != a["risk"]} | {"agent:triage"}) & labels
        if a.get("comment"):
            gh.comment(n, str(a["comment"]).strip()[:20000] + "\n\n_— ARF coordinator_")
        if a.get("dispatch") and "agent:implement" in labels:
            remove.add("agent:implement")  # re-added below to re-trigger
        gh.relabel(n, add - labels, remove)
        if a.get("dispatch"):
            gh.add_labels(n, {"agent:implement"})  # last, so the contract is in place
    briefing = (plan.get("briefing") if isinstance(plan, dict) else None) or "_The coordinator produced no briefing._"
    rogue = [p["number"] for p in state.get("pull_requests", []) if p.get("changes_workflows")]
    if rogue:
        briefing = ("### ⚠ Security\nOpen PRs change workflow files: " + ", ".join(f"#{n}" for n in rogue)
                    + ". A workflow in an unmerged PR runs with GitHub Actions' token and can write gate "
                    "statuses on other commits. Close any you didn't author.\n\n" + str(briefing))
    if rejected:
        briefing += "\n\n### Coordinator actions refused by policy\n" + "\n".join(f"- {r}" for r in rejected)
    upsert_briefing(gh, state, str(briefing))
    return rejected


def cmd_apply_plan(gh: GitHub, args):
    state = build_snapshot(gh)  # fresh and trusted; the model saw an earlier copy
    try:
        if args.plan_result != "success":
            raise OSError(f"the coordinator job ended '{args.plan_result}'")
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        upsert_briefing(gh, state, f"### Coordinator failed\nNo valid plan.json ({e}). See {run_url()}.")
        raise ArfError(f"no valid plan: {e}")
    rejected = apply_plan(gh, plan, state, rules_version(), config())
    print(f"applied; {len(rejected)} refused")
    for r in rejected:
        print("  refused:", r)


# ---------------------------------------------------------------- implement

def write_task(gh: GitHub, issue: dict, pr: dict | None, verdict_body: str | None, note: str | None) -> str:
    parts = [f"# Task: issue #{issue['number']} — {issue['title']}", "",
             "Work only on this issue. Rules: AGENTS.md and .agents/roles/implementer.md.", "",
             "## Issue (includes the ARF contract)", "", issue.get("body") or "(empty)", ""]
    comments = trusted_comments(gh.comments(issue["number"]))
    if comments:
        parts += ["## Issue comments (collaborators only, newest last)", ""]
        parts += [f"---\n{c['created_at']}:\n{c['body']}\n" for c in comments]
    if pr:
        parts += [f"## Rework of PR #{pr['number']}", "", "Current PR description:", "", pr.get("body") or "", ""]
    if note:
        parts += ["## Branch updated", "", note, ""]
    if verdict_body:
        parts += ["## Latest reviewer verdict (address every finding)", "", verdict_body, ""]
    return "\n".join(parts)


def remote_sha(gh: GitHub, branch: str) -> str:
    try:
        return gh.api(f"repos/{{repo}}/git/ref/heads/{branch}")["object"]["sha"]
    except (ArfError, KeyError, TypeError):
        return ""


def cmd_prepare_implement(gh: GitHub, args):
    """Trusted job, no model. Decides everything the later jobs must not decide themselves."""
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    cfg = config()
    OUT.mkdir(exist_ok=True)
    default = os.environ.get("ARF_DEFAULT_BRANCH") or "main"
    default_sha = remote_sha(gh, default)
    if not default_sha:
        return set_outputs(skip=f"cannot resolve default branch {default}")
    pr, verdict_body = None, None
    if "pull_request" in event:
        pr = gh.pr(event["pull_request"]["number"])
        branch = pr["head"]["ref"]
        if head_repo(pr) != gh.repo or not branch.startswith("agent/"):
            return set_outputs(skip="not an ARF agent branch in this repository")
        n = linked_issue(pr.get("body") or "") or int((re.match(r"agent/(\d+)", branch) or [0, 0])[1])
        issue = gh.issue(n)
        model = trusted_implementer(gh.statuses(pr["head"]["sha"]))
        engine = engine_of(model) or cfg["implementer"]
        model = model if engine_of(model) else cfg["models"][engine]
        _, verdict_body = latest_verdict(gh.comments(pr["number"]))
        mode, branch_sha = "rework", pr["head"]["sha"]
    else:
        issue = gh.issue(event["issue"]["number"])
        n = issue["number"]
        if issue.get("author_association") not in TRUSTED_ASSOCIATIONS:
            return set_outputs(skip="issue author is not a collaborator", issue=n)
        engine, model = cfg["implementer"], cfg["models"][cfg["implementer"]]
        branch = f"agent/{n}-{slugify(issue['title'])}"
        branch_sha = remote_sha(gh, branch)
        mode = "resume" if branch_sha else "new"
    contract = parse_contract(issue.get("body"))
    if contract and contract["duplicate_ids"]:
        return set_outputs(skip=f"the contract repeats criterion ids {contract['duplicate_ids']}; fix the contract",
                           issue=n, pr=pr["number"] if pr else "")
    if not contract or not contract["paths"] or not contract["criteria"]:
        return set_outputs(skip="issue has no ARF contract with allowed paths and numbered criteria",
                           issue=n, pr=pr["number"] if pr else "")
    if engine not in ("claude", "codex"):
        return set_outputs(skip=f"unknown implementer engine {engine!r}", issue=n)
    note = None
    if branch_sha and not is_ancestor(default_sha, branch_sha):
        note = (f"The workflow merged `{default}` ({default_sha[:12]}) into this branch so it follows rules "
                f"{rules_version()}. Re-run the required tests and update the PR description.")
    (OUT / "task.md").write_text(write_task(gh, issue, pr, verdict_body, note), encoding="utf-8")
    set_outputs(skip="", issue=n, pr=pr["number"] if pr else "", branch=branch, mode=mode, engine=engine,
                model=model, default_sha=default_sha, branch_sha=branch_sha, risk=contract.get("risk") or "")


def is_ancestor(a: str, b: str) -> bool:
    return subprocess.run(["git", *SAFE_GIT, "merge-base", "--is-ancestor", a, b],
                          capture_output=True).returncode == 0


def setup_branch(branch: str, branch_sha: str, default_sha: str, author: str, email: str):
    """Recreate the starting tree. Deterministic, so the model job and the publish job agree."""
    if branch_sha:
        git("checkout", "-q", "-B", branch, branch_sha)
        if not is_ancestor(default_sha, "HEAD"):
            r = subprocess.run(["git", *SAFE_GIT, "-c", f"user.name={author}", "-c", f"user.email={email}",
                                "merge", "--no-edit", "-q", "-m", f"Merge {default_sha[:12]} into {branch}",
                                default_sha], capture_output=True, text=True)
            if r.returncode != 0:
                git("merge", "--abort", check=False)
                raise ArfError(f"{branch} conflicts with the default branch; resolve by hand")
    else:
        git("checkout", "-q", "-B", branch, default_sha)


def cmd_setup_branch(_gh, args):
    setup_branch(args.branch, args.branch_sha, args.default_sha, args.author, args.email)
    print(git("rev-parse", "HEAD").strip())


def cmd_package_work(_gh, args):
    """Runs in the model job (untrusted). Produces data only: a patch plus the agent's text files."""
    out = Path(args.out).resolve()
    top = Path(git("rev-parse", "--show-toplevel").strip()).resolve()
    if out == top or top in out.parents:
        raise ArfError("package-work --out must be outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    exclude_arf_out()
    git("add", "-A")
    patch = git("diff", "--cached", "--binary", "--no-renames", "HEAD")
    (out / "changes.patch").write_text(patch, encoding="utf-8", errors="surrogateescape")
    for name in ("pr.md", "blocked.md"):
        src = OUT / name
        if src.is_file():
            (out / name).write_bytes(src.read_bytes()[:200_000])
    print(f"packaged {len(patch)} bytes of patch")


def exclude_arf_out():
    exclude = Path(git("rev-parse", "--git-path", "info/exclude").strip())
    exclude.parent.mkdir(parents=True, exist_ok=True)
    if ".arf-out/" not in (exclude.read_text() if exclude.exists() else ""):
        with exclude.open("a") as f:
            f.write("\n.arf-out/\n")


def report_issue(gh: GitHub, n: int, pr: int | None, text: str, add=(), remove=()):
    for target in filter(None, (n, pr)):
        gh.comment(target, text)
    gh.relabel(n, add, remove)


def push(branch: str, expected_remote_sha: str):
    token = os.environ["ARF_GH_TOKEN"]
    header = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
    git("config", "--local", "--unset-all", "http.https://github.com/.extraheader", check=False)
    lease = f"--force-with-lease=refs/heads/{branch}:{expected_remote_sha}" if expected_remote_sha else \
        f"--force-with-lease=refs/heads/{branch}:"
    git("-c", f"http.https://github.com/.extraheader={header}", "push", lease, "origin",
        f"HEAD:refs/heads/{branch}")


def cmd_publish_implement(gh: GitHub, args):
    """Fresh runner, trusted checkout of the default branch, Director's token. The artifact is data."""
    version = rules_version()
    issue = gh.issue(args.issue)
    n, pr_no = issue["number"], int(args.pr) if args.pr else None
    labels = label_names(issue)
    contract = parse_contract(issue.get("body")) or {"paths": [], "criteria": {}}
    done = ["agent:implement"] if not pr_no else []
    work = Path(args.work)
    if pr_no:
        gh.remove_label(pr_no, "agent:rework")
    if args.work_result != "success":
        report_issue(gh, n, pr_no, f"**ARF: nothing was pushed.** The implementer job ended "
                     f"'{args.work_result}', so its output is not used.\n\nRun: {run_url()}",
                     add=["needs:director"], remove=done)
        raise ArfError("work job did not succeed")

    def fail(text, label="needs:director"):
        report_issue(gh, n, pr_no, f"**ARF: nothing was pushed.** {text}\n\nRun: {run_url()}",
                     add=[label], remove=done)
        raise ArfError(text.splitlines()[0])

    patch_file = work / "changes.patch"
    patch = patch_file.read_text(encoding="utf-8", errors="surrogateescape") if patch_file.is_file() else ""
    touched, patch_errors = inspect_patch(patch)
    if patch_errors:
        fail("The implementer's patch was refused:\n" + "\n".join(f"- {e}" for e in patch_errors))
    rules_change = "rules-change" in labels
    violations = check_changed_paths(touched, contract["paths"], rules_change)
    if violations:
        fail("The implementer changed files outside its contract:\n" + "\n".join(f"- {v}" for v in violations))

    setup_branch(args.branch, args.branch_sha, args.default_sha, args.author, args.email)
    if patch.strip():
        patch_path = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "arf-changes.patch"
        patch_path.write_text(patch, encoding="utf-8", errors="surrogateescape")
        r = subprocess.run(["git", *SAFE_GIT, "apply", "--index", "--binary", "--whitespace=nowarn",
                            str(patch_path)], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"The patch did not apply to the expected starting commit: {r.stderr.strip()[:500]}")
    # Whole-branch check: everything this branch changes relative to the default branch.
    branch_files = sorted({normalize_path(p) for p in git(
        "diff", "--cached", "--name-only", "--no-renames", args.default_sha).split("\n") if p.strip()})
    violations = check_changed_paths(branch_files, contract["paths"], rules_change)
    if violations:
        fail("The branch as a whole changes files outside the contract:\n"
             + "\n".join(f"- {v}" for v in violations))

    blocked, pr_md = work / "blocked.md", work / "pr.md"
    body = None
    if not blocked.is_file():
        if not pr_md.is_file():
            fail("The implementer finished without a PR description or a blocked report.", label="blocked")
        body = pr_md.read_text(encoding="utf-8", errors="replace")
        if marker(body, "rules") != version:
            fail(f"The implementer reported rules `{marker(body, 'rules') or 'none'}`, current is `{version}`; "
                 "it did not read AGENTS.md.")
        body = set_marker(set_marker(body, "rules", version), "model", args.model)
        errors = validate_pr_body(body, version)
        if errors:
            fail("The PR description is incomplete:\n" + "\n".join(f"- {e}" for e in errors))
        if not branch_files:
            fail("The implementer wrote a PR description but the branch changes nothing.")

    # Everything is validated; only now write anything to GitHub.
    staged = git("diff", "--cached", "--name-only").strip()
    if staged:
        title = f"{issue['title']} (#{n})" if not pr_no else f"Address review findings (#{n})"
        git("-c", f"user.name={args.author}", "-c", f"user.email={args.email}", "commit", "-q", "-m",
            f"{title}\n\nAgent-Model: {args.model}\nARF-Rules: {version}")
    head = git("rev-parse", "HEAD").strip()
    moved = head != (args.branch_sha or "")
    if moved and (staged or args.branch_sha):
        push(args.branch, args.branch_sha)
        gh.set_status(head, "success", f"implementer={args.model} run={os.environ.get('GITHUB_RUN_ID', '?')}",
                      context=PROVENANCE_CONTEXT)
    if body is None:
        reason = blocked.read_text(encoding="utf-8", errors="replace")[:20000]
        note = f"\n\nCheckpoint pushed to `{args.branch}`." if staged else ""
        report_issue(gh, n, pr_no, f"**ARF implementer blocked** (`{args.model}`)\n\n{reason.strip()}{note}"
                     f"\n\nRun: {run_url()}", add=["blocked"], remove=done)
        return
    if pr_no:
        gh.api(f"repos/{{repo}}/pulls/{pr_no}", "PATCH", {"body": body})
        if not moved:
            gh.add_labels(pr_no, {"agent:review"})  # no new commit, so ask for review explicitly
        return
    pr = gh.api("repos/{repo}/pulls", "POST", {"title": issue["title"], "head": args.branch,
                                              "base": args.base_branch, "body": body})
    risk = risk_of(labels)
    gh.add_labels(pr["number"], ({f"risk:{risk}"} if risk else set()) | ({"rules-change"} & labels))
    gh.relabel(n, add=["agent:in-pr"], remove=["agent:implement", "blocked"])


# ---------------------------------------------------------------- review

def cmd_prepare_review(gh: GitHub, args):
    """Trusted job, no model. Cheap gates first; a model is only started if they pass."""
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    cfg = config()
    pr = gh.pr(event["pull_request"]["number"])
    OUT.mkdir(exist_ok=True)
    sha, body = pr["head"]["sha"], pr.get("body") or ""
    common = dict(pr=pr["number"], sha=sha)
    if pr.get("draft") or pr.get("state") != "open":
        return set_outputs(skip="draft or closed", silent="true", **common)
    if head_repo(pr) != gh.repo:
        return set_outputs(skip="fork PRs are not reviewed automatically", **common)

    def skip(reason, **flags):
        gh.set_status(sha, "failure", f"Not reviewed: {reason}")
        set_outputs(skip=reason, **flags, **common)

    branch_version = git("show", f"{sha}:.agents/VERSION", check=False).strip()
    agent_branch = pr["head"]["ref"].startswith("agent/")
    if not branch_version and not agent_branch:
        # A PR opened before ARF was installed: leave it alone (no status, no labels).
        return set_outputs(skip="branch predates ARF", silent="true", **common)
    if branch_version != rules_version():
        if agent_branch:
            return skip(f"branch follows rules {branch_version or 'none'}, current is {rules_version()}; "
                        "rework will merge the default branch", rework="true")
        return skip(f"branch follows rules {branch_version}, current is {rules_version()}; "
                    f"merge {pr['base']['ref']} into it")
    if git("ls-tree", "-r", "--name-only", sha, "--", ".arf-out").strip():
        return skip(".arf-out/ is committed in this branch; remove it (agent output must never be tracked)",
                    rework="true")
    base = git("merge-base", f"origin/{pr['base']['ref']}", sha).strip()
    files = [p for p in git("diff", "--name-only", "--no-renames", f"{base}...{sha}").split("\n") if p.strip()]
    policy = check_pr({"pull_request": pr}, rules_version(), files, require_rules_approval=False)
    if policy:  # free check; don't pay a model to find these
        return skip("policy check fails: " + "; ".join(policy)[:300], rework="true")
    implementer = trusted_implementer(gh.statuses(sha))
    if not implementer:
        return skip("no trusted provenance for this commit (not published by ARF); the Director can record it "
                    "with 'ARF Director approval' (kind: provenance)", director="true")
    if marker(body, "model") != implementer:
        print(f"::notice::PR text says {marker(body, 'model') or 'nothing'}; trusted provenance says {implementer}")
    comments = gh.comments(pr["number"])
    rounds = count_verdicts(comments)
    if rounds >= cfg["max_review_rounds"]:
        return skip(f"review round limit ({cfg['max_review_rounds']}) reached", director="true")
    n = linked_issue(body)
    contract = None
    if n:
        try:
            contract = parse_contract(gh.issue(n).get("body"))
        except ArfError:
            contract = None
    if contract and contract["duplicate_ids"]:
        return skip(f"the contract repeats criterion ids {contract['duplicate_ids']}", director="true")
    risk = max_risk(risk_of(label_names(pr)), contract and contract.get("risk"), floor_for(files, load_floors()))
    depth = DEPTH[risk]
    try:
        engine, model = pick_reviewer(implementer, cfg, depth)
    except ArfError as e:
        return skip(str(e), director="true")
    _, prev = latest_verdict(comments)
    criteria = contract["criteria"] if contract else {}
    text = [f"# Review PR #{pr['number']}: {pr['title']}", "",
            f"- Base commit: `{base}`", f"- Head commit (review exactly this): `{sha}`",
            f"- Implementer model: `{implementer}`", f"- You are: `{model}`", f"- Round: {rounds + 1}",
            f"- Risk: {risk} → **{depth} review** (see reviewer.md)", "",
            "## Acceptance criteria you must address by id", "",
            *([f"- {i}: {t}" for i, t in criteria.items()] or ["(none in the contract)"]), "",
            "## Changed files", "", *[f"- {f}" for f in files], "",
            f"## Issue #{n} contract" if n else "## No linked issue", "",
            (contract["text"] + f"\n\nAllowed paths: {contract['paths']}") if contract else "(no ARF contract)", "",
            "## PR description", "", body]
    if prev:
        text += ["", "## Previous verdict", "", prev]
    (OUT / "review.md").write_text("\n".join(text), encoding="utf-8")
    gh.set_status(sha, "pending", f"{depth.capitalize()} review by {model} in progress")
    set_outputs(skip="", engine=engine, model=model, implementer=implementer, round=rounds + 1,
                risk=risk, depth=depth, max_turns=MAX_TURNS[depth], issue=n or "",
                contract_digest=contract_digest(contract), **common)


def cmd_publish_verdict(gh: GitHub, args):
    """Fresh runner. The verdict artifact is untrusted; it is validated against the contract."""
    version, cfg = rules_version(), config()
    pr = gh.pr(args.pr)
    labels = label_names(pr)
    gh.remove_label(args.pr, "agent:review")
    if args.skip:
        gh.remove_label(args.pr, "ready-to-merge")
        if args.rework:
            gh.add_labels(args.pr, {"agent:rework"})
        elif args.director:
            gh.comment(args.pr, f"**ARF review not run:** {args.skip}. After deciding, add `agent:review` "
                       f"or `agent:rework`.\n\nRun: {run_url()}")
            gh.add_labels(args.pr, {"needs:director"})
        return
    def refuse(reason):
        gh.set_status(args.sha, "error", reason[:140])
        gh.comment(args.pr, f"**ARF review not accepted** (`{args.model}` at `{args.sha[:12]}`): {reason}"
                   f"\n\nRun: {run_url()}\nAdd `agent:review` to retry.")
        gh.relabel(args.pr, add={"needs:director"}, remove={"ready-to-merge"} & labels)
        raise ArfError(reason)

    if args.review_result != "success":
        refuse(f"the reviewer job ended '{args.review_result}', so no verdict is used")
    n = int(args.issue) if args.issue else None  # bound in prepare; the PR text may have changed since
    try:
        contract = parse_contract(gh.issue(n).get("body")) if n else None
    except ArfError:
        contract = None
    if contract and contract["duplicate_ids"]:
        refuse(f"the contract repeats criterion ids {contract['duplicate_ids']}")
    if contract_digest(contract) != args.contract_digest:
        refuse("the issue contract changed during the review; the verdict was given against a different contract")
    files = [f["filename"] for f in gh.api(f"repos/{{repo}}/pulls/{args.pr}/files?per_page=100", paginate=True) or []]
    risk = max_risk(args.risk, risk_of(labels), contract and contract.get("risk"), floor_for(files, load_floors()))
    path = Path(args.verdict)
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        errors = validate_verdict(v, version, contract["criteria"] if contract else {}, risk) \
            if isinstance(v, dict) else ["verdict is not a JSON object"]
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        v, errors = None, [f"no readable verdict.json ({e})"]
    if errors:
        gh.set_status(args.sha, "error", f"Verdict from {args.model} failed validation")
        gh.comment(args.pr, f"**ARF review not accepted as evidence** (`{args.model}` at `{args.sha[:12]}`, "
                   f"{risk} risk):\n" + "\n".join(f"- {e}" for e in errors)
                   + f"\n\nRun: {run_url()}\nAdd `agent:review` to retry.")
        gh.relabel(args.pr, add={"needs:director"}, remove={"ready-to-merge"} & labels)
        raise ArfError("invalid verdict")
    meta = {"sha": args.sha, "model": args.model, "implementer": args.implementer, "result": v["result"],
            "round": int(args.round), "risk": risk, "depth": DEPTH[risk], "contract": args.contract_digest[:16]}
    gh.comment(args.pr, render_verdict(v, meta, contract["criteria"] if contract else {}))
    current = pr["head"]["sha"] == args.sha
    if v["result"] == "ACCEPT" and not v.get("needs_director"):
        gh.set_status(args.sha, "success", f"ACCEPT by {args.model} ({DEPTH[risk]})")
        if current:
            gh.relabel(args.pr, add={"ready-to-merge"} - labels, remove={"agent:rework"} & labels)
            if auto_merge_allowed(cfg["auto_merge"], risk):
                gh._run(["pr", "merge", str(args.pr), "--auto", "--squash", "--repo", gh.repo])
        return
    gh.set_status(args.sha, "failure", "Needs Director decision" if v.get("needs_director")
                  else f"REJECT by {args.model}")
    if not current:
        return
    gh.remove_label(args.pr, "ready-to-merge")
    if v.get("needs_director") or int(args.round) >= cfg["max_review_rounds"]:
        gh.add_labels(args.pr, {"needs:director"})
    else:
        gh.add_labels(args.pr, {"agent:rework"})


# ---------------------------------------------------------------- policy and approvals

def cmd_check_pr(gh: GitHub, args):
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    changed = [normalize_path(l) for l in Path(args.changed_files).read_text().splitlines() if l.strip()] \
        if args.changed_files else []
    statuses = gh.statuses(event["pull_request"]["head"]["sha"]) if any(is_protected(p) for p in changed) else []
    errors = check_pr(event, rules_version(Path(args.base_dir)), changed, statuses)
    for e in errors:
        print(f"::error::{e}")
    if errors:
        raise SystemExit(1)
    print("PASS: PR description and paths follow ARF (content is verified by the cross-model review)")


def require_director() -> str:
    director = (os.environ.get("ARF_DIRECTOR") or "").strip()
    actor = (os.environ.get("GITHUB_ACTOR") or "").strip()
    triggering = (os.environ.get("GITHUB_TRIGGERING_ACTOR") or actor).strip()
    if not director:
        raise ArfError("ARF_DIRECTOR repository variable is not set; refusing to record a Director approval")
    if actor.casefold() != director.casefold() or triggering.casefold() != director.casefold():
        raise ArfError(f"only the Director ({director}) may approve; this run was started by {triggering or '?'}")
    return director


def cmd_director_approval(gh: GitHub, args):
    director = require_director()
    pr = gh.pr(args.pr)
    if pr["head"]["sha"] != args.sha:
        raise ArfError(f"PR head is {pr['head']['sha']}, not {args.sha}; approve the commit you actually reviewed")
    run = os.environ.get("GITHUB_RUN_ID", "?")
    if args.kind == "rules":
        gh.set_status(args.sha, "success", f"Rules change approved by {director} (run {run})", context=RULES_CONTEXT)
    elif args.kind == "provenance":
        gh.set_status(args.sha, "success", f"implementer=human approved-by={director} run={run}",
                      context=PROVENANCE_CONTEXT)
    else:
        raise ArfError("kind must be rules or provenance")
    print(f"recorded {args.kind} approval by {director} at {args.sha}")


def cmd_report(gh: GitHub, args):
    gh.comment(args.target, f"**ARF:** {args.text}\n\nRun: {run_url()}")
    gh.relabel(args.target, add=[l for l in args.add.split(",") if l],
               remove=[l for l in args.remove.split(",") if l])


# ---------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, *required, **optional):
        s = sub.add_parser(name)
        for a in required:
            s.add_argument(a, required=True)
        for a, default in optional.items():
            s.add_argument("--" + a.replace("_", "-"), default=default)
        return s

    add("labels")
    add("snapshot", out=str(OUT / "state.json"))
    add("apply-plan", plan=str(OUT / "plan.json"), plan_result="")
    add("prepare-implement", "--event")
    add("setup-branch", "--branch", "--default-sha", "--author", "--email", branch_sha="")
    add("package-work", out="arf-work")
    add("publish-implement", "--issue", "--branch", "--model", "--base-branch", "--author", "--email",
        "--default-sha", "--work", pr="", branch_sha="", work_result="")
    add("prepare-review", "--event")
    add("publish-verdict", "--pr", "--sha", model="", implementer="", round="1", verdict="verdict/verdict.json",
        skip="", director="", rework="", review_result="", issue="", contract_digest="none", risk="")
    add("check-pr", "--event", base_dir=".", changed_files="")
    add("director-approval", "--pr", "--sha", "--kind")
    add("report", "--target", "--text", add="", remove="")
    args = p.parse_args(argv)
    try:
        globals()["cmd_" + args.cmd.replace("-", "_")](GitHub(), args)
    except ArfError as e:
        print(f"::error::{e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
