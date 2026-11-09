# RevisionWorx - lightweight, offline engineering PDM
# Copyright (C) 2026 RevisionWorx contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Package workflow drafts a version tag's release and nothing more (docs/RELEASE_CHECKLIST.md, steps 3 and 4).

Read as text, without a YAML library: the core and its tests need only the standard library and pytest.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / ".github" / "workflows" / "package.yml"


def jobs(text: str) -> dict[str, str]:
    """Each job's text, by its id (the two-space-indented keys under `jobs:`)."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([A-Za-z0-9_-]+):\n", body, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


@pytest.fixture(scope="module")
def package() -> str:
    return PACKAGE.read_text(encoding="utf-8")


def test_only_the_draft_job_can_write_and_only_on_a_version_tag(package):
    top = package.split("\njobs:\n", 1)[0]
    assert re.search(r"^permissions:\n  contents: read\n", top, re.M), "the workflow's token can only read"
    found = jobs(package)
    assert set(found) == {"build", "python-packages", "draft-release"}
    writers = [name for name, body in found.items() if "contents: write" in body]
    assert writers == ["draft-release"]
    draft = found["draft-release"]
    assert "    if: startsWith(github.ref, 'refs/tags/v')\n" in draft
    assert "    needs: [build, python-packages]\n" in draft


def test_the_draft_job_checks_before_it_drafts_and_never_publishes(package):
    draft = jobs(package)["draft-release"]
    checks = draft.index('if [ "$GITHUB_REF_NAME" != "v$version" ]')
    dated = draft.index('grep -Eq "^## $version \\([0-9]{4}-[0-9]{2}-[0-9]{2}\\)$" CHANGELOG.md')
    sums = draft.index("sha256sum -c -- *.sha256")
    whole = draft.index("sha256sum -c SHA256SUMS")
    create = draft.index("gh release create")
    assert checks < dated < sums < whole < create, "every check comes before the draft"
    command = draft[create:]
    assert "--draft" in command and "--verify-tag" in command
    for name in ("RevisionWorx-$version-windows-x64.zip", "RevisionWorx-$version-linux-x86_64.tar.gz",
                 "revisionworx-$version-py3-none-any.whl", "revisionworx-$version.tar.gz", "SHA256SUMS"):
        assert name in draft, name
    # Publishing is a person's step; tags are never made or moved here.
    for never in ("--draft=false", "gh release edit", "gh release upload", "git tag", "git push", "gh api"):
        assert never not in package, never


def test_the_changelog_check_matches_a_dated_section_only():
    pattern = r"^## 1.0.0 \([0-9]{4}-[0-9]{2}-[0-9]{2}\)$"
    assert re.search(pattern, "## 1.0.0 (2026-11-08)\n", re.M)
    assert not re.search(pattern, "## 1.0.0 (unreleased: awaiting the Director's release decision)\n", re.M)


def test_the_changelog_dates_1_0_0():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(r"^## 1\.0\.0 \([0-9]{4}-[0-9]{2}-[0-9]{2}\)$", changelog, re.M)


def step(job: str, name: str) -> str:
    """The shell of the step called `name` in `job`'s text: its `run: |` block, unindented."""
    after = job[job.index(f"- name: {name}"):]
    block = after[after.index("run: |\n") + len("run: |\n"):]
    lines = []
    for line in block.splitlines():
        if line.strip() and not line.startswith(" " * 10):
            break
        lines.append(line[10:])
    return "\n".join(lines).strip() + "\n"


def test_python_packages_sums_dont_list_their_own_file(package):
    """`sha256sum * | tee SHA256SUMS` could list SHA256SUMS itself, half written, when tee wins the race."""
    sums = step(jobs(package)["python-packages"], "Checksums")
    assert "sha256sum * | tee SHA256SUMS" not in sums
    assert sums == 'cd dist\nsums="$(sha256sum -- *)"\nprintf \'%s\\n\' "$sums" | tee SHA256SUMS\n'


BASH = bool(shutil.which("bash")) and subprocess.run(["bash", "-c", "command -v sha256sum"],
                                                    capture_output=True).returncode == 0


@pytest.mark.skipif(not BASH, reason="needs bash and sha256sum")
def test_the_draft_job_checks_the_sums_the_builds_wrote(package, tmp_path):
    """The workflow's own Checksums step, then the draft job's checks, on files as the builds name and sum them."""
    found = jobs(package)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "revisionworx-1.0.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "revisionworx-1.0.0.tar.gz").write_bytes(b"sdist")
    made = subprocess.run(["bash", "-eo", "pipefail", "-c", step(found["python-packages"], "Checksums")],
                          cwd=tmp_path, capture_output=True, text=True)
    assert made.returncode == 0, made.stdout + made.stderr
    listed = (dist / "SHA256SUMS").read_text().split()
    assert listed[1::2] == ["revisionworx-1.0.0-py3-none-any.whl", "revisionworx-1.0.0.tar.gz"]

    release = tmp_path / "release"
    release.mkdir()
    for f in dist.iterdir():
        (release / f.name).write_bytes(f.read_bytes())
    # The standalone builds' sums: Windows' sha256sum writes binary-mode lines ("hash *name"), Linux's "hash  name".
    for name, mark in (("RevisionWorx-1.0.0-windows-x64.zip", "*"), ("RevisionWorx-1.0.0-linux-x86_64.tar.gz", " ")):
        (release / name).write_bytes(name.encode())
        digest = hashlib.sha256(name.encode()).hexdigest()
        (release / f"{name}.sha256").write_text(f"{digest} {mark}{name}\n")
    checks = "cd release\nsha256sum -c -- *.sha256\nsha256sum -c SHA256SUMS\n"
    assert all(line in found["draft-release"] for line in checks.splitlines()[1:])
    ok = subprocess.run(["bash", "-eo", "pipefail", "-c", checks], cwd=tmp_path, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    (release / "revisionworx-1.0.0.tar.gz").write_bytes(b"changed after it was summed")
    bad = subprocess.run(["bash", "-eo", "pipefail", "-c", checks], cwd=tmp_path, capture_output=True, text=True)
    assert bad.returncode != 0, "a file that doesn't match its sum stops the draft"
