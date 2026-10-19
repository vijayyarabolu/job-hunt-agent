"""The public version's setup: keys saved locally (0600, never echoed), Settings > Setup's routes and states, the
"Connect Google" flow from the web UI (PKCE, redirect to the app's own port, Disconnect revokes), the built-in resume
tailor, profiles made from Settings, and demo mode. No network: Google is the local fake, Claude a stub."""
import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from job_hunt_agent import (claude_api, config, demo, profile_admin, rgclient, sample, secrets_store, server,
                            setup_status, tailor)
from job_hunt_agent.google import accounts, auth
from job_hunt_agent.mail import claude as mail_claude
from tests.google_case import GoogleCase
from tests.test_mail_gmail import redirect_target

CLIENT_JSON = json.dumps({"installed": {"client_id": "1234-abc.apps.googleusercontent.com",
                                        "client_secret": "GOCSPX-test-secret-1234"}})


class TempHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [mock.patch.object(config, "CONFIG", self.root / "config"),
                        mock.patch.object(config, "DATA", self.root / "data"),
                        mock.patch.object(config, "PROFILES", self.root / "profiles"),
                        mock.patch.object(config, "DB_PATH", self.root / "data" / "t.db")]
        for p in self.patches:
            p.start()
        (self.root / "profiles").mkdir()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class SecretsTest(TempHome):
    def test_a_key_is_saved_owner_only_and_never_shown_back(self):
        secrets_store.put("anthropic_api_key", "sk-ant-test-0000-abcd")
        path = config.CONFIG / "secrets.yaml"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(secrets_store.get("anthropic_api_key"), "sk-ant-test-0000-abcd")
        self.assertEqual(secrets_store.masked("sk-ant-test-0000-abcd"), "saved, ends in abcd")
        items = {i["id"]: i for i in setup_status.items(None)}
        self.assertEqual(items["anthropic"]["state"], "connected")
        self.assertNotIn("sk-ant-test", json.dumps(items))
        secrets_store.put("anthropic_api_key", "")
        self.assertIsNone(secrets_store.get("anthropic_api_key"))
        self.assertEqual({i["id"]: i for i in setup_status.items(None)}["anthropic"]["state"], "missing")

    def test_unknown_names_are_refused(self):
        with self.assertRaises(ValueError):
            secrets_store.put("password", "x")
        with self.assertRaises(ValueError):
            secrets_store.save_setting("anything", "x")

    def test_a_persons_own_provider_key_wins_over_the_shared_one(self):
        from job_hunt_agent.finder import keys
        secrets_store.put("hunter_api_key", "shared-hunter-key")
        self.assertEqual(keys.key("hunter", "alex"), "shared-hunter-key")
        (config.CONFIG / "secrets.yaml").write_text("hunter_api_key: shared-hunter-key\nhunter:\n  alex_api_key: mine\n")
        self.assertEqual(keys.key("hunter", "alex"), "mine")
        self.assertEqual(keys.key("hunter", "jordan"), "shared-hunter-key")

    def test_every_item_says_why_and_what_works_without_it(self):
        for item in setup_status.items(None):
            self.assertTrue(item["why"] and item["without"], item["id"])
            self.assertIn(item["state"], ("connected", "missing", "failing", "optional", "demo"))

    def test_a_failed_test_shows_failing_until_the_key_changes(self):
        secrets_store.put("anthropic_api_key", "sk-ant-wrong")
        with mock.patch.object(claude_api, "test", return_value=(False, "Anthropic refused this key (401)")):
            self.assertFalse(setup_status.test("anthropic", None)["ok"])
        self.assertEqual({i["id"]: i for i in setup_status.items(None)}["anthropic"]["state"], "failing")
        setup_status.save_key("anthropic_api_key", "sk-ant-right")
        self.assertEqual({i["id"]: i for i in setup_status.items(None)}["anthropic"]["state"], "connected")


class ClaudeApiTest(TempHome):
    def test_structured_output_schemas_are_closed(self):
        out = claude_api.strict(mail_claude.SCHEMA)
        entry = out["properties"]["results"]["items"]
        self.assertFalse(out["additionalProperties"])
        self.assertFalse(entry["additionalProperties"])
        self.assertEqual(entry["properties"]["application"], {"anyOf": [{"type": "integer"}, {"type": "null"}]})

    def test_no_key_is_a_login_problem_not_a_crash(self):
        self.assertFalse(claude_api.configured())
        with self.assertRaises(claude_api.ClaudeError) as caught:
            claude_api.ask("hi")
        self.assertEqual(caught.exception.kind, "login")
        self.assertFalse(claude_api.test()[0])

    def test_with_a_key_the_email_reader_uses_the_api(self):
        secrets_store.put("anthropic_api_key", "sk-ant-test")
        with mock.patch.object(claude_api, "ask", return_value=({"results": []}, {"input_tokens": 5})) as ask, \
                mock.patch.object(mail_claude, "run_cli", side_effect=AssertionError("no CLI with a key")):
            self.assertEqual(mail_claude.run_claude("prompt", "alex")[:2], ({"results": []}, "ok"))
        self.assertEqual(ask.call_args.args[1], mail_claude.SYSTEM)


class TailorTest(TempHome):
    def setUp(self):
        super().setUp()
        sample.write(config.PROFILES)
        self.base = mock.patch.object(rgclient, "_base", lambda: "")
        self.base.start()
        self.jd = ("We need a machine learning engineer with Python, PyTorch, Kubernetes and MLflow to ship ranking "
                   "models. Requirements: 3+ years, AWS, Docker.")

    def tearDown(self):
        self.base.stop()
        super().tearDown()

    def queue(self) -> int:
        with mock.patch.object(tailor, "_start_worker"):
            ids = rgclient.request("POST", "/api/jobs", {"jobs": [{"profile": "alex", "company": "Acme",
                                                                  "role": "ML Engineer", "jd_text": self.jd,
                                                                  "source_job_id": "lever:acme:1"}]})["ids"]
        return ids[0]

    def run_one(self, job_id: int, answer: dict | None) -> dict:
        job = tailor._load(job_id)
        with mock.patch.object(claude_api, "configured", return_value=answer is not None), \
                mock.patch.object(claude_api, "ask", return_value=(answer, {})):
            done = tailor._finish(job, tailor._tailor(job))
        tailor._save(done)
        return done

    def test_without_a_service_url_the_built_in_tailor_answers_the_same_api(self):
        job_id = self.queue()
        jobs = rgclient.request("GET", "/api/jobs?profile=alex&lean=1")["jobs"]
        self.assertEqual([(j["id"], j["status"], j["source_job_id"]) for j in jobs], [(job_id, "queued", "lever:acme:1")])
        base = tailor.base_resume("alex")
        answer = {"headline": "Machine Learning Engineer", "summary": "ML engineer who ships ranking models.",
                  "skills": [{"category": s["category"], "items": list(reversed(s["items"]))} for s in base["skills"]],
                  "experience": [{"company": j["company"], "bullets": ["Shipped ranking models with PyTorch."]}
                                 for j in base["experience"]]}
        done = self.run_one(job_id, answer)
        self.assertEqual(done["status"], "ready")
        tailored = done["tailored"]
        for old, new in zip(base["experience"], tailored["experience"]):          # same jobs, same bullet counts
            self.assertEqual(len(old["bullets"]), len(new["bullets"]))
            self.assertEqual(new["bullets"][0], "Shipped ranking models with PyTorch.")
            self.assertEqual(new["bullets"][1:], old["bullets"][1:])               # unanswered bullets kept
        for old, new in zip(base["skills"], tailored["skills"]):
            self.assertEqual(sorted(old["items"]), sorted(new["items"]))          # reordered, never added
        pdf, kind = rgclient.request("GET", f"/api/jobs/{job_id}/file/pdf", raw=True)
        self.assertEqual((pdf[:5], kind), (b"%PDF-", "application/pdf"))
        docx_bytes, _ = rgclient.request("GET", f"/api/jobs/{job_id}/file/docx", raw=True)
        path = self.root / "t.docx"
        path.write_bytes(docx_bytes)
        from job_hunt_agent.resume import baseline
        self.assertEqual(baseline.parse(path)["headline"], "Machine Learning Engineer")
        detail = rgclient.request("GET", f"/api/jobs/{job_id}")["job"]
        self.assertGreater(detail["result"]["diff"]["changed_bullets"], 0)
        self.assertIn("tailored_pct", detail["result"]["ats"])
        rgclient.request("POST", f"/api/jobs/{job_id}/approve", {})
        self.assertTrue(rgclient.request("GET", f"/api/jobs/{job_id}")["job"]["result"]["approved"])

    def test_no_key_fails_the_job_with_a_message(self):
        job_id = self.queue()
        job = tailor._load(job_id)
        with self.assertRaisesRegex(ValueError, "Anthropic API key"):
            tailor._tailor(job)
        with self.assertRaisesRegex(ValueError, "resume-tailoring service"):
            rgclient.request("POST", f"/api/jobs/{job_id}/gdoc", {})


class ProfileAdminTest(TempHome):
    def test_a_profile_is_made_and_its_resume_read(self):
        out = profile_admin.create({"name": "Sam Example", "families": ["data_scientist"], "years": "3",
                                    "location": "Denver, CO"})
        self.assertEqual(out["id"], "sam")
        self.assertEqual(config.profile("sam")["families"], ["data_scientist"])
        with self.assertRaisesRegex(ValueError, "already"):
            profile_admin.create({"name": "Sam Again", "id": "sam"})
        data = sample.resume_docx("Sam Example", "sam@example.com", "Data Scientist", "Builds models.")
        got = profile_admin.upload_resume("sam", "Sam_Example.docx", data)
        self.assertEqual(got["jobs"], 3)
        self.assertTrue((config.PROFILES / "sam" / "baseline.json").exists())
        with self.assertRaisesRegex(ValueError, ".docx"):
            profile_admin.upload_resume("sam", "resume.pdf", b"%PDF-1.4")

    def test_bad_fields_are_refused(self):
        for bad in ({"name": ""}, {"name": "X", "families": ["astronaut"]}, {"name": "Y", "years": "ninety"}):
            with self.assertRaises(ValueError):
                profile_admin.create(bad)


class ConnectGoogleTest(GoogleCase):
    """Settings > Setup: save your own OAuth client, Connect Google (consent on the fake), back on this port."""
    connect = False

    def setUp(self):
        super().setUp()
        (self.root / "config" / "google_client.json").unlink()
        self.httpd = server.make_server(port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(self.base + path, method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Jobhunt": "1"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            with err:
                return err.code, json.loads(err.read() or b"{}")

    def setup_items(self) -> dict:
        with urllib.request.urlopen(f"{self.base}/api/setup?profile=jane", timeout=10) as resp:
            return {i["id"]: i for i in json.loads(resp.read())["items"]}

    def test_client_then_connect_then_disconnect(self):
        self.assertEqual(self.setup_items()["google_client"]["state"], "missing")
        self.assertEqual(self.post("/api/setup/google_connect", {})[0], 400)          # no client yet: a message
        self.assertEqual(self.post("/api/setup/google_client", {"client_id": "nope", "client_secret": "x"})[0], 400)
        self.assertEqual(self.post("/api/setup/google_client", {"json": CLIENT_JSON})[0], 200)
        client_file = self.root / "config" / "google_client.json"
        self.assertEqual(stat.S_IMODE(client_file.stat().st_mode), 0o600)
        items = self.setup_items()
        self.assertEqual(items["google_client"]["state"], "connected")
        self.assertEqual(items["google"]["redirect_uri"], f"http://127.0.0.1:{self.httpd.server_port}/")
        status, out = self.post("/api/setup/google_connect", {})
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(out["url"]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], [f"http://127.0.0.1:{self.httpd.server_port}/"])
        self.assertEqual(query["scope"][0].split(), list(auth.SCOPES))
        answer = redirect_target(out["url"])                        # the consent screen sends the browser back
        self.assertTrue(answer.startswith(f"http://127.0.0.1:{self.httpd.server_port}/?"))
        with urllib.request.urlopen(answer, timeout=10) as resp:
            self.assertIn("Connected as", resp.read().decode())
        self.assertTrue(accounts.main_status()["connected"])
        self.assertEqual(self.setup_items()["google"]["state"], "connected")
        with urllib.request.urlopen(answer, timeout=10) as resp:     # the same answer works once
            self.assertNotIn("Connected as", resp.read().decode())
        self.assertEqual(self.post("/api/setup/google_disconnect", {}), (200, {"disconnected": True}))
        self.assertFalse(accounts.main_status()["connected"])
        self.assertFalse(auth.token_path().exists())

    def test_demo_mode_saves_nothing(self):
        with mock.patch.dict(os.environ, {"JOB_HUNT_AGENT_DEMO": "1"}):
            self.assertEqual(self.post("/api/setup/key", {"name": "anthropic_api_key", "value": "k"})[0], 403)
            self.assertTrue(self.post("/api/setup/test", {"item": "anthropic"})[1]["ok"])
            self.assertEqual(self.setup_items()["anthropic"]["state"], "demo")
        self.assertIsNone(secrets_store.get("anthropic_api_key"))


class DemoTest(unittest.TestCase):
    def test_demo_seeds_jobs_profiles_a_tracker_and_tailored_resumes(self):
        before = config.ROOT
        try:
            with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"JOB_HUNT_AGENT_DEMO": "1"}):
                out = demo.seed(Path(tmp))
                self.assertGreaterEqual(out["jobs"], 20)
                self.assertEqual(config.profile_ids(), ["alex", "jordan"])
                from job_hunt_agent import api, db
                conn = db.connect()
                feed = api.jobs(conn, "alex", "recommended", db.utcnow(), wait=True)
                self.assertGreater(len(feed["jobs"]), 10)
                statuses = {r["status"] for r in conn.execute("SELECT status FROM applications")}
                self.assertTrue({"applied", "interview", "rejected"} <= statuses)
                conn.close()
                self.assertEqual(len(list(tailor.folder().glob("*.docx"))), 2)
        finally:
            config.set_home(before)


if __name__ == "__main__":
    unittest.main()
