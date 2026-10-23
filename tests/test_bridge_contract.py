"""GOAL34 §5: the job-hunt-agent ⇄ resumegen bridge. resumegen's jhscripts/*.py run under job-hunt-agent's venv and import job_hunt_agent
as a library; GOAL33 §3c removed lexicon.category_of as "unused" and every resume's ATS check failed the next morning.
Every job-hunt-agent name those scripts use is pinned here, with the arguments they pass. When the resumegen repo is on this
Mac (RESUMEGEN_SRC, else ~/Documents/resumegen), its scripts are read too, so a name it starts using must be added
here; and jh_score.py itself is run against this checkout the way resumegen runs it (-I, JSON on stdin)."""
import ast
import importlib
import inspect
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESUMEGEN = Path(os.environ.get("RESUMEGEN_SRC", str(Path.home() / "Documents" / "resumegen")))
SCRIPTS = RESUMEGEN / "resumegen" / "jhscripts"
RUN_TIMEOUT_S = 120

# "module.name": the number of positional arguments resumegen passes (None: a value, not a call)
CONTRACT = {
    "job_hunt_agent.lexicon.find": 1, "job_hunt_agent.lexicon.category_of": 1,
    "job_hunt_agent.keywords.pack": 3, "job_hunt_agent.resumes.Resume": 8, "job_hunt_agent.resumes.normalized": 1,
    "job_hunt_agent.titles.match": 1, "job_hunt_agent.vendor_model.score": 3, "job_hunt_agent.resume.ats.extract": 1,
    "job_hunt_agent.resume_place.resolve": 3, "job_hunt_agent.resume_place.city_state": 1, "job_hunt_agent.resume_place.places": 1,
    "job_hunt_agent.config.profile": 1, "job_hunt_agent.config.profile_ids": 0, "job_hunt_agent.config.PROFILES": None,
}


def used_by(script: Path) -> set[str]:
    """Every job-hunt-agent module.name a script uses (`from job_hunt_agent import x` then x.name, or `from job_hunt_agent.y import x`)."""
    tree = ast.parse(script.read_text())
    alias = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] == "job_hunt_agent":
            for a in n.names:
                alias[a.asname or a.name] = f"{n.module}.{a.name}"
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in alias:
            out.add(f"{alias[n.value.id]}.{n.attr}")
    return out


class ContractTest(unittest.TestCase):
    def test_every_name_resumegen_uses_is_here(self):
        for name, args in CONTRACT.items():
            with self.subTest(name):
                module, attr = name.rsplit(".", 1)
                value = getattr(importlib.import_module(module), attr, None)
                self.assertIsNotNone(value, f"{name} is gone: resumegen's jhscripts call it")
                if args is not None:
                    inspect.signature(value).bind(*([None] * args))   # TypeError: resumegen's call no longer fits

    @unittest.skipUnless(SCRIPTS.is_dir(), "the resumegen repo is not on this machine")
    def test_the_list_covers_resumegen_as_it_is(self):
        used = set().union(*(used_by(p) for p in sorted(SCRIPTS.glob("*.py"))))
        self.assertTrue(used, f"no job-hunt-agent names found in {SCRIPTS}")
        self.assertEqual(sorted(used - set(CONTRACT)), [], "resumegen uses job-hunt-agent names this contract does not pin")

    @unittest.skipUnless((SCRIPTS / "jh_score.py").is_file(), "the resumegen repo is not on this machine")
    def test_jh_score_runs_on_this_checkout(self):
        # resumegen runs `<job-hunt-agent venv python> -I jh_score.py`; here this checkout's job-hunt-agent goes first on the path
        boot = ("import sys, runpy; sys.path.insert(0, sys.argv[1]); import job_hunt_agent; "
                "assert job_hunt_agent.__file__.startswith(sys.argv[1]), job_hunt_agent.__file__; "
                "sys.argv = sys.argv[2:]; runpy.run_path(sys.argv[0], run_name='__main__')")
        payload = {"jd_text": "AI Engineer. Python, PyTorch, RAG and SQL on AWS. You build LLM evaluation systems.",
                   "title": "AI Engineer", "company": "ZZ Test Corp", "resumes": [
                       {"id": "ai", "label": "AI Engineer", "families": ["ai_engineer"],
                        "text": "Zz Testcase. AI Engineer. Python, PyTorch, RAG, SQL, AWS. Built LLM evaluation."}]}
        proc = subprocess.run([sys.executable, "-I", "-c", boot, str(ROOT), str(SCRIPTS / "jh_score.py")],
                              input=json.dumps(payload), capture_output=True, text=True, timeout=RUN_TIMEOUT_S,
                              cwd=str(ROOT), env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        out = json.loads(proc.stdout)
        self.assertEqual(out["best_id"], "ai")
        self.assertTrue(out["scores"])
        self.assertTrue(any(out["categories"].values()), out["categories"])


if __name__ == "__main__":
    unittest.main()
