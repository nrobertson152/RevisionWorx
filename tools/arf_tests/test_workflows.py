"""Static checks on the workflow files: the token-isolation boundary and the wiring between jobs.

Needs PyYAML (preinstalled on GitHub-hosted runners); skipped where it is missing.
"""
import pathlib
import re
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("arf-*.yml"))  # the project's own workflows are not ARF's to police
MODEL_ACTIONS = ("anthropics/claude-code-action", "openai/codex-action")


def load(path):
    return yaml.safe_load(path.read_text())


def is_model_job(job):
    return any(any(a in (s.get("uses") or "") for a in MODEL_ACTIONS) for s in job["steps"])


@unittest.skipIf(yaml is None, "PyYAML not installed")
class WorkflowTests(unittest.TestCase):
    def jobs(self):
        for wf in WORKFLOWS:
            for name, job in load(wf)["jobs"].items():
                yield wf.name, name, job

    def test_no_model_job_can_reach_the_directors_token(self):
        for wf, name, job in self.jobs():
            if is_model_job(job):
                text = yaml.dump(job)
                self.assertNotIn("ARF_GH_TOKEN", text, f"{wf}:{name}")
                self.assertFalse(any(v == "write" for v in (job.get("permissions") or {}).values()),
                                 f"{wf}:{name} has write permissions")
                for s in job["steps"]:
                    if "actions/checkout" in (s.get("uses") or ""):
                        self.assertIs(s["with"].get("persist-credentials"), False, f"{wf}:{name}")

    def test_publish_jobs_run_no_model_and_no_repo_scripts(self):
        for wf, name, job in self.jobs():
            if "ARF_GH_TOKEN" in yaml.dump(job):
                self.assertFalse(is_model_job(job), f"{wf}:{name}")
                for s in job["steps"]:
                    run = s.get("run") or ""
                    self.assertNotIn("ARF_BOOTSTRAP_CMD", run + str(s.get("env")), f"{wf}:{name}")

    def test_every_job_declares_permissions(self):
        for wf in WORKFLOWS:
            data = load(wf)
            if data.get("permissions") == {}:
                for name, job in data["jobs"].items():
                    self.assertIn("permissions", job, f"{wf.name}:{name}")

    def test_needs_outputs_resolve(self):
        for wf in WORKFLOWS:
            jobs = load(wf)["jobs"]
            declared = {j: set((jb.get("outputs") or {})) for j, jb in jobs.items()}
            for name, job in jobs.items():
                needs = job.get("needs") or []
                needs = [needs] if isinstance(needs, str) else needs
                for dep, out in re.findall(r"needs\.([a-z_]+)\.outputs\.([a-z_]+)", yaml.dump(job)):
                    self.assertIn(dep, needs, f"{wf.name}:{name} uses needs.{dep} without depending on it")
                    self.assertIn(out, declared[dep], f"{wf.name}:{name} needs.{dep}.outputs.{out}")

    def test_director_approval_is_restricted_to_the_director(self):
        job = load(ROOT / ".github/workflows/arf-director-approval.yml")["jobs"]["approve"]
        cond = job["if"]
        for needle in ("github.actor == vars.ARF_DIRECTOR", "github.triggering_actor == vars.ARF_DIRECTOR",
                       "vars.ARF_DIRECTOR != ''", "default_branch"):
            self.assertIn(needle, cond)
        self.assertIn("ARF_DIRECTOR", job["env"])  # the tool re-checks the identity itself

    def test_only_trusted_jobs_can_write_gate_statuses(self):
        for wf, name, job in self.jobs():
            if (job.get("permissions") or {}).get("statuses") == "write":
                self.assertFalse(is_model_job(job), f"{wf}:{name}")
                self.assertNotIn("ARF_BOOTSTRAP_CMD", yaml.dump(job), f"{wf}:{name}")

    def test_model_jobs_start_clean_and_hand_over_only_on_success(self):
        for wf, name, job in self.jobs():
            if not is_model_job(job):
                continue
            steps = job["steps"]
            first_model = next(i for i, s in enumerate(steps) if any(a in (s.get("uses") or "") for a in MODEL_ACTIONS))
            clears = [i for i, s in enumerate(steps) if "rm -rf .arf-out" in (s.get("run") or "")]
            self.assertTrue(clears and clears[0] < first_model, f"{wf}:{name} must clear .arf-out before the model")
            for s in steps[first_model:]:
                if "upload-artifact" in (s.get("uses") or "") or "package-work" in (s.get("run") or ""):
                    self.assertNotIn("always()", str(s.get("if", "")), f"{wf}:{name}: hand over only on success")

    def test_publish_jobs_check_the_model_jobs_result(self):
        for wf in WORKFLOWS:
            jobs = load(wf)["jobs"]
            model_jobs = [n for n, j in jobs.items() if is_model_job(j)]
            for name, job in jobs.items():
                needs = job.get("needs") or []
                for m in model_jobs:
                    if m in needs and "ARF_GH_TOKEN" in yaml.dump(job):
                        self.assertIn(f"needs.{m}.result", yaml.dump(job), f"{wf.name}:{name}")

    def test_no_expressions_inside_run_scripts(self):
        for wf, name, job in self.jobs():
            for s in job["steps"]:
                run = s.get("run") or ""
                self.assertNotRegex(run, r"\$\{\{\s*(steps|needs|inputs|github\.event)\.",
                                    f"{wf}:{name}:{s.get('name')} — pass values through env instead")


if __name__ == "__main__":
    unittest.main()
