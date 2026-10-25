"""GOAL8 §1-§3 against a throwaway DB, a synthetic profile and a fake resumegen on a local port: status chips,
Generate (one POST, JD from the stored snapshot, busy jobs skipped, 20 max), the side panel's ownership check,
"The resume service is offline", outside jobs under Saved, Apply ↗ -> "Did you apply?" (newest first, Later, No, 7-day drop)
and Yes (applied, tailored or base resume attached, only the empty Sheet columns asked)."""
import http.client
import json
import re
import tempfile
import threading
import unittest
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs

import docx

from job_hunt_agent import api, applyback, config, db, outside, resume_upload, rgclient, rgjobs, server, store, trackapi
from job_hunt_agent.ats.common import FetchResult, make_job

NOW = db.utcnow().replace(microsecond=0)
PID = "tester"
TOKEN = "rg-test-token"
JD = "Requirements:\n" + "Build ML systems in Python and PyTorch, deploy on Kubernetes and AWS. " * 4
PROFILE = """name: Tess Tester
families: [ml_engineer]
years: 4
max_required_years: 4
resumes:
  - {id: ml, label: ML Engineer, file: Tess_ML.docx, families: [ml_engineer]}
"""


ANSWER = ("I build retrieval systems in Python and keep them honest with evaluations. This role asks for the same "
          "work on a bigger product, and that is why I applied.")


class FakeResumegen:
    """resumegen's HTTP API, in memory."""

    def __init__(self):
        self.jobs, self.posts, self.actions, self.down = {}, [], [], False
        self.files, self.file_gets = {}, []          # (job id, which) -> (bytes, type); every file GET (GOAL3 §4)
        self.answers, self.answer_text = {}, ANSWER  # GOAL29 §5: written answers (ready on the first status read)
        self.events = []                             # GOAL33 §1: what /api/events streams (a job added or changed)
        self.moved = threading.Condition()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload, ctype="application/json"):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _ok(self):
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    self._send(401, {"ok": False, "error": "token required"})
                    return False
                return True

            def _control(self, body=None):
                """Test-only knobs for the browser driver: /__state, /__set {id, ...}, /__add {...}, /__down."""
                if self.path == "/__state":
                    return self._send(200, {"posts": fake.posts, "actions": fake.actions, "jobs": fake.jobs})
                if self.path == "/__set":
                    job_id = int(body.pop("id"))
                    fake.jobs[job_id].update(body)
                    fake.event(job_id)
                elif self.path == "/__add":
                    body = {"id": fake.add(**body)}
                elif self.path == "/__down":
                    fake.down = True
                return self._send(200, body or {})

            def do_GET(self):
                if self.path.startswith("/__"):
                    return self._control()
                if fake.down:
                    return self._send(503, {"ok": False, "error": "down"})
                if not self._ok():
                    return
                path, _, query = self.path.partition("?")
                if path == "/api/events":                             # GOAL33 §1: like resumegen's stream
                    return self._events()
                m = re.match(r"^/api/answers/(\d+)$", path)
                if m:
                    return self._send(200, {"ok": True, "answer": fake.answer_view(int(m.group(1)), finish=True)})
                if path == "/api/jobs":
                    pid = parse_qs(query).get("profile", [""])[0]
                    jobs = [{k: v for k, v in j.items() if k != "jd_text"} for j in fake.jobs.values()
                            if j["profile"] == pid]
                    return self._send(200, {"ok": True, "jobs": sorted(jobs, key=lambda j: -j["id"])})
                m = re.match(r"^/api/jobs/(\d+)(/file/([a-z.]+))?$", path)
                if not m or int(m.group(1)) not in fake.jobs:
                    return self._send(404, {"ok": False, "error": "no such job"})
                job = fake.jobs[int(m.group(1))]
                if m.group(3):
                    fake.file_gets.append((int(m.group(1)), m.group(3)))
                    if fake.files.get((int(m.group(1)), m.group(3)), "") is None:     # the run's file is gone
                        return self._send(404, {"ok": False, "error": "no such job or file"})
                    return self._send(200, *fake.files.get((int(m.group(1)), m.group(3)),
                                                           (b"%PDF-1.4 tailored", "application/pdf")))
                return self._send(200, {"ok": True, "job": {k: v for k, v in job.items() if k != "jd_text"},
                                        "jd_text": job.get("jd_text")})

            def _events(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                seen = len(fake.events)
                try:
                    while not fake.down:
                        with fake.moved:
                            fake.moved.wait_for(lambda: len(fake.events) > seen or fake.down, timeout=1)
                            new = fake.events[seen:]
                        for e in new:
                            self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        seen += len(new)
                except OSError:
                    pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path.startswith("/__"):
                    return self._control(body)
                if fake.down:
                    return self._send(503, {"ok": False, "error": "down"})
                if not self._ok():
                    return
                if self.path == "/api/jobs":
                    ids = [fake.add(**item) for item in body["jobs"]]
                    fake.posts.append(body["jobs"])
                    return self._send(201, {"ok": True, "ids": ids})
                if self.path == "/api/answers":                       # GOAL29 §5: like resumegen, queued first
                    return self._send(200, {"ok": True, "answer": fake.answer_view(fake.add_answer(body))})
                if self.path == "/api/answers/lookup":
                    return self._send(200, {"ok": True, "answers": fake.lookup(body)})
                if self.path == "/api/unarchive":                     # resumegen GOAL3 §6: Restore
                    fake.actions.append((None, "unarchive", body))
                    for i in body["ids"]:
                        fake.jobs[i]["archived_at"] = None
                    return self._send(200, {"ok": True, "restored": body["ids"]})
                m = re.match(r"^/api/jobs/(\d+)/([a-z_]+)$", self.path)
                fake.actions.append((int(m.group(1)), m.group(2), body))
                job = fake.jobs.get(int(m.group(1)))
                if job and m.group(2) in ("regenerate", "fix", "retry"):   # like resumegen: back in the queue
                    job.update(status="queued", archived_at=None)
                if job and m.group(2) == "cancel" and job["status"] in ("queued", "waiting", "generating", "checking"):
                    job["status"] = "cancelled"                       # like resumegen: a waiting one stops
                if job and m.group(2) == "archive":                   # like resumegen: hidden, kept
                    job["archived_at"] = "2026-10-13T05:00:00Z"
                if job and m.group(2) == "approve":                   # like resumegen: uploaded to Drive
                    name = f"{job['company']}_{job['role']}.pdf".replace(" ", "_")
                    job.update(drive_file_id=f"F{job['id']}", drive_link=f"https://drive.example/F{job['id']}", drive_name=name,
                               result={**(job.get("result") or {}), "approved": {"link": f"https://drive.example/F{job['id']}", "name": name}})
                if job:
                    fake.event(job["id"])
                return self._send(200, {"ok": True})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def add(self, profile, company, role, jd_text, jd_url=None, source_job_id=None, status="queued", **extra):
        job_id = max(self.jobs, default=0) + 1
        self.jobs[job_id] = {"id": job_id, "profile": profile, "company": company, "role": role, "jd_text": jd_text,
                             "jd_url": jd_url, "source_job_id": source_job_id, "status": status, "error": None,
                             "attempts": 0, "elapsed_s": None, "drive_file_id": None, "drive_link": None,
                             "drive_name": None, "gdoc_link": None, "result": {}, **extra}
        self.event(job_id)
        return job_id

    def event(self, job_id: int) -> None:
        with self.moved:
            self.events.append({"id": len(self.events) + 1, "job_id": job_id, "profile": self.jobs[job_id]["profile"],
                                "kind": "status", "text": self.jobs[job_id]["status"]})
            self.moved.notify_all()

    def add_answer(self, body: dict) -> int:
        answer_id = max(self.answers, default=0) + 1
        self.answers[answer_id] = {**body, "id": answer_id, "ready": False}
        return answer_id

    def answer_view(self, answer_id: int, finish: bool = False) -> dict:
        row = self.answers[answer_id]
        if finish and not row["ready"]:
            text = self.answer_text + (" Shorter." if row.get("variant") == "shorter" else "")
            row.update(ready=True, text=text)
        out = {"id": answer_id, "question": row["question"], "variant": row.get("variant") or ""}
        if row["ready"]:
            return {**out, "state": "ready", "label": "Ready", "text": row["text"], "words": len(row["text"].split())}
        return {**out, "state": "writing", "label": "Writing with Claude…"}

    def lookup(self, body: dict) -> dict:
        out = {}
        for answer_id, row in sorted(self.answers.items()):
            if (row["profile"], row["job_key"]) == (body["profile"], body["job_key"]) and row["question"] in body["questions"]:
                out[row["question"]] = self.answer_view(answer_id)
        return out

    def stop(self):
        with self.moved:
            self.down = True
            self.moved.notify_all()
        self.srv.shutdown()
        self.srv.server_close()


def seed_job(conn, ext_id: str, title: str = "Machine Learning Engineer", description: str = JD) -> str:
    item = make_job(ats="lever", key="acme", ext_id=ext_id, company="Acme", title=title, location="Austin, TX",
                    url=f"https://jobs.lever.co/acme/{ext_id}", posted_at=db.iso(NOW), description=description)
    db.sync_boards(conn, [{"board": "lever:acme", "ats": "lever", "key": "acme", "company": "Acme"}])
    run = db.start_run(conn, "test", NOW)
    store.write_board(conn, db.board(conn, "lever:acme"), FetchResult([item], 200, n_raw=1), "ok", run, NOW,
                      ["fam:ml_engineer"], 5)
    return item["uid"]


class Goal8Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "profiles" / PID / "resumes").mkdir(parents=True)
        (root / "profiles" / PID / "profile.yaml").write_text(PROFILE)
        (root / "profiles" / PID / "resumes" / "Tess_ML.pdf").write_bytes(b"%PDF-1.4 base")
        doc = docx.Document()
        doc.add_paragraph("Tess Tester. ML Engineer. Python, PyTorch, Kubernetes, AWS. 2021 - 2025.")
        doc.save(root / "profiles" / PID / "resumes" / "Tess_ML.docx")
        self.rg = FakeResumegen()
        self.patches = [mock.patch.object(config, "DB_PATH", root / "t.db"),
                        mock.patch.object(config, "PROFILES", root / "profiles"),
                        mock.patch.object(rgclient, "_base", lambda: self.rg.url),
                        mock.patch.object(rgclient, "_token", lambda: TOKEN)]
        for p in self.patches:
            p.start()
        self.conn = db.connect()
        self.uids = [seed_job(self.conn, str(n)) for n in range(1, 4)]

    def tearDown(self):
        self.conn.close()
        for p in reversed(self.patches):
            p.stop()
        self.rg.stop()
        self.tmp.cleanup()


class ChipsAndGenerateTest(Goal8Case):
    def test_each_resumegen_status_maps_to_one_of_four_chips_newest_job_wins(self):
        for status, uid in (("waiting", "a"), ("checking", "b"), ("failed", "c"), ("cancelled", "d")):
            self.rg.add(PID, "Co", "Role", JD, source_job_id=uid, status=status)
        self.rg.add(PID, "Co", "Role", JD, source_job_id="a", status="needs_you", error="page 2 has 3 lines")
        self.rg.add("someone_else", "Co", "Role", JD, source_job_id="b", status="ready")
        got = rgjobs.statuses(self.conn, PID)
        self.assertTrue(got["online"])
        self.assertEqual({u: c["chip"] for u, c in got["jobs"].items()},
                         {"a": "needs you", "b": "generating", "c": "needs you"})
        self.assertEqual(got["jobs"]["a"]["why"], "page 2 has 3 lines")
        self.assertEqual(got["active"], 2)                       # waiting + checking (the other person's not counted)

    def test_generate_for_selected_sends_one_post_with_the_stored_jd(self):
        app_id = trackapi.create(self.conn, PID, {"job_uid": self.uids[0], "status": "saved"})["id"]
        with db.tx(self.conn):
            self.conn.execute("INSERT OR REPLACE INTO jd_snapshots(app_id, text, state) VALUES (?, ?, 'saved')",
                              (app_id, "SNAPSHOT " + JD))
        out = rgjobs.generate(self.conn, PID, self.uids)
        self.assertEqual(out["queued"], 3)
        self.assertEqual(len(self.rg.posts), 1)
        sent = {j["source_job_id"]: j for j in self.rg.posts[0]}
        self.assertTrue(sent[self.uids[0]]["jd_text"].startswith("SNAPSHOT"))     # the person's kept JD
        self.assertIn("Kubernetes", sent[self.uids[1]]["jd_text"])                # the feed's JD
        self.assertEqual(sent[self.uids[1]]["jd_url"], "https://jobs.lever.co/acme/2")
        self.assertEqual({k for k in sent[self.uids[1]]},
                         {"profile", "company", "role", "jd_text", "jd_url", "source_job_id", "workplace", "location"})  # GOAL11 §4
        self.assertEqual(set(out["chips"]), set(self.uids))
        again = rgjobs.generate(self.conn, PID, self.uids[:1])                   # still queued: not sent twice
        self.assertEqual((again["queued"], again["skipped"][0]["why"]), (0, "already in the queue"))

    def test_generate_refuses_more_than_twenty_and_empty_picks(self):
        with self.assertRaisesRegex(ValueError, "at most 20"):
            rgjobs.generate(self.conn, PID, [f"u{n}" for n in range(21)])
        for bad in ([], None, "abc", [1]):
            with self.assertRaises(ValueError):
                rgjobs.generate(self.conn, PID, bad)

    def test_a_job_without_jd_text_is_skipped_with_the_reason(self):
        bare = seed_job(self.conn, "9", description="")
        out = rgjobs.generate(self.conn, PID, [bare, self.uids[0]])
        self.assertEqual(out["queued"], 1)
        self.assertIn("no JD text", out["skipped"][0]["why"])

    def test_a_job_without_jd_text_reads_its_posting_once_and_keeps_it(self):
        # ~1,500 Workday jobs in Alex's feed were first seen when > 2 days old, so their JD was never read and
        # Generate refused them; the posting is now read once (robots-gated) and its JD kept on the job
        from job_hunt_agent import jdsnap
        bare = seed_job(self.conn, "9", description="")
        with mock.patch.object(jdsnap, "fetch_text", return_value=("saved", JD, "workday")) as read:
            out = rgjobs.generate(self.conn, PID, [bare])
        self.assertEqual((out["queued"], out["skipped"]), (1, []))
        self.assertEqual(read.call_count, 1)
        kept = self.conn.execute("SELECT description FROM jobs WHERE uid = ?", (bare,)).fetchone()[0]
        self.assertIn("PyTorch", db.unpack(kept))
        self.assertIn("PyTorch", self.rg.posts[-1][0]["jd_text"])

    def test_the_panel_only_opens_the_persons_own_resume(self):
        mine = self.rg.add(PID, "Acme", "MLE", JD, status="ready",
                           result={"ats": {"base_pct": 60, "tailored_pct": 74}, "diff": {"changed_bullets": 3},
                                   "files": {"pdf": "/x.pdf", "png": "/x.png"}})
        other = self.rg.add("someone_else", "Acme", "MLE", JD, status="ready")
        panel = rgjobs.detail(PID, mine)
        self.assertEqual((panel["ats"]["base_pct"], panel["ats"]["tailored_pct"]), (60, 74))
        self.assertEqual(panel["files"], ["pdf", "png"])
        with self.assertRaises(KeyError):
            rgjobs.detail(PID, other)
        with self.assertRaises(KeyError):
            rgjobs.action(PID, other, "approve")
        self.assertEqual(rgjobs.file(PID, mine, "pdf")[0], b"%PDF-1.4 tailored")
        rgjobs.action(PID, mine, "regenerate", "more MLOps")
        self.assertEqual(self.rg.actions[-1], (mine, "regenerate", {"note": "more MLOps"}))

    def test_resumegen_down_reads_resume_app_is_offline(self):
        self.rg.stop()
        got = rgjobs.statuses(self.conn, PID)
        self.assertEqual((got["online"], got["message"]), (False, "The resume service is offline"))
        with self.assertRaisesRegex(rgclient.Offline, "The resume service is offline"):
            rgjobs.generate(self.conn, PID, self.uids[:1])
        self.rg = FakeResumegen()                                  # tearDown stops it again


class OutsideJobTest(Goal8Case):
    def test_a_pasted_jd_becomes_a_saved_outside_row_once(self):
        rg_id = self.rg.add(PID, "Recruiter Co", "AI Engineer", JD, jd_url="https://example.com/post", status="ready")
        self.rg.add(PID, "Gone Co", "AI Engineer", JD, status="cancelled")
        self.assertEqual(rgjobs.statuses(self.conn, PID)["outside_new"], 1)
        self.assertEqual(rgjobs.statuses(self.conn, PID)["outside_new"], 0)         # adopted once
        rows = outside.saved_rows(self.conn, PID)
        self.assertEqual([(r["company"], r["role_title"], r["rg_id"]) for r in rows],
                         [("Recruiter Co", "AI Engineer", rg_id)])
        app = trackapi.get(self.conn, PID, rows[0]["id"])
        self.assertEqual((app["status"], app["job_url"]), ("saved", "https://example.com/post"))
        self.assertIn("Kubernetes", app["jd_text"])
        self.assertEqual(rgjobs.statuses(self.conn, PID)["apps"][rows[0]["id"]]["chip"], "ready")
        saved = api.jobs(self.conn, PID, "saved", NOW)              # GOAL36 §5.1: its job record's card, tagged
        self.assertEqual([(c["company"], c.get("outside_job")) for c in saved["jobs"] if c.get("outside_job")],
                         [("Recruiter Co", True)])
        self.assertEqual((saved["outside"], saved["outside_cards"]), ([], 1))

    def test_a_pasted_jd_for_a_role_already_tracked_links_to_that_row(self):
        app_id = trackapi.create(self.conn, PID, {"company": "Acme", "role_title": "MLE", "status": "saved"})["id"]
        self.rg.add(PID, "acme ", "mle", JD, status="queued")
        rgjobs.statuses(self.conn, PID)
        self.assertEqual(list(outside.links(self.conn, PID).values()), [app_id])

    @mock.patch.object(resume_upload, "upload")
    def test_mark_applied_on_an_outside_job_applies_it_with_its_resume(self, upload):
        upload.return_value = {"name": "Tess_Tester_AI_Engineer_Resume(Recruiter Co).pdf", "link": "https://d/1",
                               "sheet": "not on the Sheet yet"}
        self.rg.add(PID, "Recruiter Co", "AI Engineer", JD, status="ready")
        rgjobs.statuses(self.conn, PID)
        app_id = outside.saved_rows(self.conn, PID)[0]["id"]
        out = applyback.confirm(self.conn, PID, NOW, app_id=app_id)
        self.assertEqual(trackapi.get(self.conn, PID, app_id)["status"], "applied")
        self.assertEqual(out["attached"]["kind"], "tailored")              # its ready resume approved first
        self.assertEqual([a[1] for a in self.rg.actions], ["approve"])
        self.assertEqual(outside.saved_rows(self.conn, PID), [])                    # left Saved


class RaceTest(Goal8Case):
    """Code review of d67c0b7: two tabs (or the phone and the laptop) acting at the same moment."""

    def test_two_polls_at_once_adopt_an_outside_job_once(self):
        self.rg.add(PID, "Recruiter Co", "AI Engineer", JD, status="ready")
        jobs = rgjobs.listing(PID)
        conns = [db.connect() for _ in range(4)]
        threads = [threading.Thread(target=outside.sync, args=(c, PID, jobs)) for c in conns]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for c in conns:
            c.close()
        rows = self.conn.execute("SELECT count(*) FROM applications WHERE company = 'Recruiter Co'").fetchone()[0]
        self.assertEqual((rows, len(outside.links(self.conn, PID))), (1, 1))

    def test_placeholder_names_never_merge_pasted_jds(self):
        self.rg.add(PID, "", "", JD, status="ready")
        self.rg.add(PID, "Unknown", "Role", JD, status="ready")
        rgjobs.statuses(self.conn, PID)
        self.assertEqual(len(set(outside.links(self.conn, PID).values())), 2)

    def test_a_pasted_jd_never_replaces_the_kept_jd_of_a_row_it_matches(self):
        app_id = trackapi.create(self.conn, PID, {"company": "Acme", "role_title": "MLE", "status": "saved"})["id"]
        with db.tx(self.conn):
            self.conn.execute("INSERT OR REPLACE INTO jd_snapshots(app_id, text, state) VALUES (?, 'KEPT JD', 'saved')",
                              (app_id,))
        self.rg.add(PID, "Acme", "MLE", "PASTED " + JD, status="ready")
        rgjobs.statuses(self.conn, PID)
        kept = self.conn.execute("SELECT text FROM jd_snapshots WHERE app_id = ?", (app_id,)).fetchone()[0]
        self.assertEqual(kept, "KEPT JD")

    def test_a_job_resumegen_lost_mid_poll_is_skipped(self):
        rg_id = self.rg.add(PID, "Gone Co", "AI Engineer", JD, status="ready")
        jobs = rgjobs.listing(PID)
        del self.rg.jobs[rg_id]
        self.assertEqual(outside.sync(self.conn, PID, jobs), 0)

    @mock.patch.object(resume_upload, "upload")
    def test_two_yes_answers_at_once_mark_and_attach_once(self, upload):
        upload.return_value = {"name": "Tess_Tester_ML_Resume(Acme).pdf", "link": "https://drive/b", "sheet": "x"}
        entry = applyback.remember(self.conn, PID, NOW, uid=self.uids[0])
        results = []

        def answer():
            c = db.connect()
            try:
                results.append(applyback.answer(c, PID, entry, "yes", NOW)["state"])
            except ValueError as err:
                results.append(str(err))
            finally:
                c.close()
        threads = [threading.Thread(target=answer) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["already answered (yes)"] * 2 + ["yes"])
        self.assertEqual(upload.call_count, 1)

    def test_peek_changes_nothing(self):
        old = applyback.remember(self.conn, PID, NOW - timedelta(days=8), uid=self.uids[0])
        self.assertEqual(applyback.due(self.conn, PID, NOW, boot=True, peek=True)["dropped"], [])
        self.assertEqual([e["id"] for e in applyback.due(self.conn, PID, NOW, boot=True)["dropped"]], [old])

    def test_a_yes_that_crashes_before_marking_is_put_back(self):
        entry = applyback.remember(self.conn, PID, NOW, uid=self.uids[0])
        with mock.patch.object(applyback.applydialog, "mark", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                applyback.answer(self.conn, PID, entry, "yes", NOW)
        self.assertEqual([e["id"] for e in applyback.due(self.conn, PID, NOW)["ask"]], [entry])


class DidYouApplyTest(Goal8Case):
    def entry_ids(self, boot=False):
        return [e["id"] for e in applyback.due(self.conn, PID, NOW, boot=boot)["ask"]]

    def test_several_apply_clicks_are_asked_newest_first_and_a_second_click_asks_again(self):
        first = applyback.remember(self.conn, PID, NOW - timedelta(minutes=30), uid=self.uids[0])
        second = applyback.remember(self.conn, PID, NOW - timedelta(minutes=10), uid=self.uids[1])
        self.assertEqual(self.entry_ids(), [second, first])
        self.assertEqual(applyback.remember(self.conn, PID, NOW, uid=self.uids[0]), first)   # one entry per job
        self.assertEqual(self.entry_ids(), [first, second])

    def test_later_waits_for_the_next_app_open_and_no_keeps_the_job(self):
        later = applyback.remember(self.conn, PID, NOW, uid=self.uids[0])
        nope = applyback.remember(self.conn, PID, NOW, uid=self.uids[1])
        applyback.answer(self.conn, PID, later, "later", NOW)
        applyback.answer(self.conn, PID, nope, "no", NOW)
        self.assertEqual(self.entry_ids(), [])                      # coming back to the tab: not asked again
        self.assertEqual(self.entry_ids(boot=True), [later])        # opening the app: asked again
        self.assertEqual(applyback.due(self.conn, PID, NOW)["snoozed"], 1)
        self.assertIsNone(self.conn.execute("SELECT status FROM applications WHERE job_uid = ? AND status = "
                                            "'applied'", (self.uids[1],)).fetchone())
        with self.assertRaisesRegex(ValueError, "already answered"):
            applyback.answer(self.conn, PID, nope, "yes", NOW)

    def test_entries_older_than_seven_days_drop_off_with_a_note_once_never_applied(self):
        old = applyback.remember(self.conn, PID, NOW - timedelta(days=8), uid=self.uids[0])
        got = applyback.due(self.conn, PID, NOW, boot=True)
        self.assertEqual(([e["id"] for e in got["dropped"]], got["ask"]), ([old], []))
        self.assertEqual(applyback.due(self.conn, PID, NOW, boot=True)["dropped"], [])
        row = self.conn.execute("SELECT status FROM applications WHERE job_uid = ?", (self.uids[0],)).fetchone()
        self.assertNotEqual((row or {"status": None})["status"], "applied")

    def test_another_persons_entry_cannot_be_answered(self):
        mine = applyback.remember(self.conn, PID, NOW, uid=self.uids[0])
        with self.assertRaises(KeyError):
            applyback.answer(self.conn, "someone_else", mine, "yes", NOW)

    @mock.patch.object(resume_upload, "link_existing")
    @mock.patch.object(resume_upload, "upload")
    def test_yes_marks_applied_with_the_approved_tailored_resume(self, upload, link):
        link.return_value = {"name": "Tess_Tester_MLE_Resume(Acme).pdf", "link": "https://drive/t", "sheet": "x"}
        self.rg.add(PID, "Acme", "MLE", JD, source_job_id=self.uids[0], status="ready", drive_file_id="F1",
                    drive_link="https://drive/t", drive_name="Tess_Tester_MLE_Resume(Acme).pdf")
        entry = applyback.remember(self.conn, PID, NOW, uid=self.uids[0])
        out = applyback.answer(self.conn, PID, entry, "yes", NOW)
        row = trackapi.get(self.conn, PID, out["app"])
        self.assertEqual((row["status"], row["applied_on"]), ("applied", NOW.astimezone().date().isoformat()))
        self.assertEqual(out["attached"]["kind"], "tailored")
        link.assert_called_once()
        self.assertEqual(link.call_args.args[3:], ("Tess_Tester_MLE_Resume(Acme).pdf", "F1", "https://drive/t"))
        upload.assert_not_called()
        self.assertEqual(row["address"], "Austin, TX")                          # what the app knows is filled
        self.assertEqual(self.entry_ids(), [])

    @mock.patch.object(resume_upload, "upload")
    def test_yes_without_a_tailored_resume_uploads_the_base_resume_even_when_resumegen_is_down(self, upload):
        upload.return_value = {"name": "Tess_Tester_ML_Resume(Acme).pdf", "link": "https://drive/b", "sheet": "x"}
        self.rg.stop()
        entry = applyback.remember(self.conn, PID, NOW, uid=self.uids[0])
        out = applyback.answer(self.conn, PID, entry, "yes", NOW)
        self.assertEqual(out["attached"]["kind"], "base")
        self.assertIn("offline", out["attached"]["note"])
        self.assertEqual(upload.call_args.args[3:], ("Tess_ML.pdf", b"%PDF-1.4 base"))
        self.rg = FakeResumegen()

    @mock.patch.object(resume_upload, "upload", side_effect=ValueError("Google Drive: token expired"))
    def test_a_failed_attach_keeps_the_applied_mark_with_the_resume_missing_chip(self, _):
        entry = applyback.remember(self.conn, PID, NOW, uid=self.uids[0])
        out = applyback.answer(self.conn, PID, entry, "yes", NOW)
        row = trackapi.get(self.conn, PID, out["app"])
        self.assertEqual((row["status"], row["resume_missing"]), ("applied", True))
        self.assertEqual(out["attached"], {"kind": "none", "error": "Google Drive: token expired"})

    def test_the_form_asks_only_for_empty_typed_columns(self):
        row = {"sheet": {"columns": [{"header": h, "field": f} for h, f in (
            ("Company Name", "company"), ("Date Applied", "date_applied"), ("Address", "address"),
            ("Target Name", "target_name"), ("Target Role", "target_role"), ("Email Sent", "email_sent"),
            ("Follow-Up 1", "follow_up_1"), ("Column 1", None), ("Team Name", "team_name"))],
            "cells": ["Acme", None, "Austin, TX", None, "Recruiter", None, None, None, ""]}}
        self.assertEqual(applyback.missing(row), [{"field": "target_name", "header": "Target Name"},
                                                  {"field": "team_name", "header": "Team Name"}])


class ServerRoutesTest(Goal8Case):
    def setUp(self):
        super().setUp()
        self.srv = server.make_server(lan=False, port=0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {"Host": "localhost", "X-Jobhunt": "1", "Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_apply_click_remembers_and_pending_answers(self):
        status, _ = self.call("POST", "/api/apply_click", {"profile": PID, "uid": self.uids[0]})
        self.assertEqual(status, 200)
        status, data = self.call("GET", f"/api/pending?profile={PID}")
        ask = json.loads(data)["ask"]
        self.assertEqual([(e["company"], e["role"]) for e in ask], [("Acme", "Machine Learning Engineer")])
        status, data = self.call("POST", "/api/pending/answer", {"profile": PID, "id": ask[0]["id"], "answer": "no"})
        self.assertEqual((status, json.loads(data)["state"]), (200, "no"))

    def test_offline_resumegen_is_a_503_and_the_rest_keeps_working(self):
        self.rg.stop()
        status, data = self.call("POST", "/api/resumes/generate", {"profile": PID, "uids": self.uids[:1]})
        self.assertEqual((status, json.loads(data)), (503, {"error": "The resume service is offline", "offline": True}))
        status, data = self.call("GET", f"/api/resumes?profile={PID}")
        self.assertEqual((status, json.loads(data)["online"]), (200, False))
        self.assertEqual(self.call("GET", f"/api/jobs?profile={PID}&tab=recommended")[0], 200)
        self.rg = FakeResumegen()

    def test_resume_files_pass_through_as_pdf(self):
        rg_id = self.rg.add(PID, "Acme", "MLE", JD, status="ready", result={"files": {"pdf": "/x.pdf"}})
        status, data = self.call("GET", f"/api/resumes/file?profile={PID}&id={rg_id}&which=pdf")
        self.assertEqual((status, data), (200, b"%PDF-1.4 tailored"))
        self.assertEqual(self.call("GET", f"/api/resumes/file?profile={PID}&id={rg_id}&which=docx")[0], 400)

    def test_settings_years_cap(self):
        status, data = self.call("POST", "/api/settings/years", {"profile": PID, "cap": 3})
        self.assertEqual((status, json.loads(data)), (200, {"years": 4, "max_required_years": 3, "feed_min_match": 0,
                                                               "cold_after_days": 30, "mail_daily_at": "00:15",
                                                               "past_employers": [], "past_employers_default": []}))
        self.assertEqual(self.call("POST", "/api/settings/years", {"profile": PID, "cap": "x"})[0], 400)

    def test_settings_minimum_match(self):
        """resumegen GOAL3 §8: the floor defaults to 0 (every matched job) and either person can raise it."""
        self.assertEqual(json.loads(self.call("GET", f"/api/settings?profile={PID}")[1])["feed_min_match"], 0)
        status, data = self.call("POST", "/api/settings/min_match", {"profile": PID, "min_match": 40})
        self.assertEqual((status, json.loads(data)["feed_min_match"]), (200, 40))
        self.assertIn("feed_min_match: 40", (config.PROFILES / PID / "profile.yaml").read_text())
        for bad in ("x", 101, -5, True):
            self.assertEqual(self.call("POST", "/api/settings/min_match", {"profile": PID, "min_match": bad})[0], 400)
        self.call("POST", "/api/settings/min_match", {"profile": PID, "min_match": 0})


if __name__ == "__main__":
    unittest.main()
