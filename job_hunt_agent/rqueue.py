"""the Resumes tab, one place from "Generate resume" to "applied".

Built from resumegen's list (rgjobs, the existing bridge) and the person's tracker, three views:
  - queue: running first ("generating now"), then queued in the order they will run (#1 runs next; resumegen's
    "Move to top" priority, else first in first out), then waiting (paused: usage limit or sign-in).
  - ready: a finished resume ("needs you" at the top) for a job not applied to and not passed on, newest ready first
; a closed posting drops to the bottom (§2c).
  - done: applied today, among the jobs that have a resume here (Undo is the tracker's own).
Feed jobs carry their usual feed card (so the job list, keys and detail pane work as on every tab); outside jobs
(JDs pasted in the Resume app) a small card with `outside` = their tracker row.

§2c: a queued (not yet running) generation whose posting closed is cancelled on the next look (saves Claude usage).
§2d: applied today vs the person's daily goal.
"""
import logging
from datetime import datetime

from . import api, daygroups, outside, removal, rgclient, rgjobs
from .db import tx
from .google import state as gstate

log = logging.getLogger(__name__)
VIEWS = ("queue", "ready", "done")
RUNNING = ("generating", "checking")
WAITING = ("queued", "waiting")
FINISHED = ("ready", "needs_you", "failed")
PRE_APPLY = ("saved", "tailored")


today = api.today


def _entries(conn, pid: str, jobs: list[dict]) -> dict[str, dict]:
    """key -> the newest resumegen job that was not cancelled; key = feed uid, or 'app:<row id>' for an outside job."""
    links = outside.links(conn, pid)
    out: dict[str, dict] = {}
    for job in sorted(jobs, key=lambda j: j["id"]):
        if job.get("status") == "cancelled":
            continue
        key = job.get("source_job_id") or (f"app:{links[job['id']]}" if job["id"] in links else None)
        if key:
            out[key] = job
    return out


def _apps(conn, pid: str) -> tuple[dict, dict]:
    rows = [dict(r) for r in conn.execute(
        "SELECT id, job_uid, company, role_title, job_url, address, status, applied_on, coalesce(resume, '') <> '' "
        "OR EXISTS (SELECT 1 FROM resume_uploads u WHERE u.app_id = applications.id AND u.removed_at IS NULL) AS cv "
        "FROM applications WHERE profile = ?", (pid,))]
    return {r["job_uid"]: r for r in rows if r["job_uid"]}, {r["id"]: r for r in rows}


def _app_of(key: str, by_uid: dict, by_id: dict) -> dict | None:
    return by_id.get(int(key[4:])) if key.startswith("app:") else by_uid.get(key)


def _closed(conn, uids: list[str]) -> set[str]:
    if not uids:
        return set()
    marks = ",".join("?" * len(uids))
    return {r["uid"] for r in conn.execute(f"SELECT uid FROM jobs WHERE closed_at IS NOT NULL AND uid IN ({marks})",
                                           uids)}


def queue_order(jobs: list[dict]) -> list[dict]:
    """The person's live resumegen jobs as they will run: running, queued (#1 next), waiting."""
    running = sorted((j for j in jobs if j.get("status") in RUNNING), key=lambda j: j.get("started_at") or "")
    queued = sorted((j for j in jobs if j.get("status") == "queued"), key=lambda j: (-(j.get("priority") or 0), j["id"]))
    waiting = sorted((j for j in jobs if j.get("status") == "waiting"), key=lambda j: j["id"])
    return running + queued + waiting


def positions(jobs: list[dict]) -> dict[int, int]:
    """resumegen id -> place in the queue (1 = runs next); running jobs are not numbered."""
    return {j["id"]: n for n, j in enumerate((j for j in queue_order(jobs) if j.get("status") in WAITING), 1)}


def sweep(conn, pid: str, jobs: list[dict]) -> list[dict]:
    """§2c: cancel every queued generation whose posting closed; returns those jobs. A refusal or Offline is logged
    and tried again on the next look."""
    waiting = [j for j in jobs if j.get("status") in WAITING and j.get("source_job_id")]
    closed = _closed(conn, [j["source_job_id"] for j in waiting])
    done = []
    for job in (j for j in waiting if j["source_job_id"] in closed):
        try:
            rgjobs.action(pid, job["id"], "cancel")
            done.append(job)
        except (rgclient.Offline, KeyError, ValueError) as err:
            log.warning("could not cancel resumegen job %s (posting closed): %s", job["id"], err)
    return done


def _view(job: dict, app: dict | None, day: str, dismissed: bool) -> str | None:
    if app and app["status"] not in PRE_APPLY:
        return "done" if app.get("applied_on") == day else None
    if job.get("archived_at"):                   # archived or deleted in resumegen leaves Queue and Ready
        return None
    status = job.get("status")
    if status in RUNNING or status in WAITING:
        return "queue"
    return "ready" if status in FINISHED and not dismissed else None


def classify(conn, pid: str, jobs: list[dict], now: datetime) -> dict[str, list]:
    """view -> [(key, resumegen job, tracker row or None, closed)] in display order."""
    entries = _entries(conn, pid, jobs)
    by_uid, by_id = _apps(conn, pid)
    triage, day = api._triage(conn, pid), today(now)
    closed = _closed(conn, [k for k in entries if not k.startswith("app:")])
    views: dict[str, list] = {v: [] for v in VIEWS}
    removed = removal.hidden(conn, pid)[0]                     # × Remove hides it here too
    for key, job in entries.items():
        if key in removed:
            continue
        app = _app_of(key, by_uid, by_id)
        view = _view(job, app, day, triage.get(key) == "dismissed")
        if view:
            views[view].append((key, job, app, key in closed))
    order = {j["id"]: n for n, j in enumerate(queue_order([j for _, j, _, _ in views["queue"]]))}
    views["queue"].sort(key=lambda e: order[e[1]["id"]])
    newest = sorted(views["ready"], key=lambda e: (e[1].get("finished_at") or "", e[1]["id"]), reverse=True)
    views["ready"] = sorted(newest, key=lambda e: (e[3], e[1].get("status") == "ready"))     # stable: newest first within
    views["done"].sort(key=lambda e: -(e[2] or {}).get("id", 0))
    return views


progress = api.progress                     # §2d: lives in api (the feed answers with it too)


def counts(views: dict[str, list]) -> dict[str, int]:
    return {"queue": len(views["queue"]), "ready": len(views["ready"]), "done": len(views["done"]),
            "needs": sum(1 for _, j, _, _ in views["ready"] if j.get("status") != "ready")}


def _rq(job: dict, app: dict | None, view: str, closed: bool, pos: dict) -> dict:
    status = job.get("status")
    state = {"queue": "generating" if status in RUNNING else status,
             "ready": "ready" if status == "ready" else "needs"}.get(view, view)
    return {"rg": job["id"], "state": state, "pos": pos.get(job["id"]), "started_at": job.get("started_at"),
            "elapsed_s": job.get("elapsed_s"), "ready_at": job.get("finished_at"), "why": job.get("error"),
            "approved": bool(job.get("drive_file_id")), "closed": closed, "app": (app or {}).get("id"),
            "cv": bool((app or {}).get("cv")), "made_at": made_at(job, view)}   # the row has a resume (×)


def made_at(job: dict, view: str) -> str | None:
    """the time a row is grouped by: queued (Queue), ready (Ready), approved (Done today), else made."""
    times = job.get("times") or {}
    if view == "queue":
        return times.get("queued") or job.get("created_at")
    if view == "done":
        return times.get("approved") or times.get("made") or job.get("finished_at")
    return job.get("finished_at") or times.get("made")


def _outside_card(key: str, job: dict, app: dict | None) -> dict:
    """A small card for an outside job (or a feed job no longer in the DB)."""
    app = app or {}
    url = app.get("job_url") or job.get("jd_url")
    return {"uid": key, "outside": app.get("id"), "company": app.get("company") or job.get("company"),
            "title": app.get("role_title") or job.get("role"), "location": app.get("address"), "url": url,
            "apply_url": url, "match": None, "worth": None, "posted_ts": None,
            "app": {"id": app["id"], "status": app["status"]} if app else None}


def board(conn, pid: str, view: str, now: datetime) -> dict:
    """GET /api/jobs?tab=resumes&rq=<view>: the cards of one view, in order, with every view's count."""
    if view not in VIEWS:
        raise ValueError(f"rq must be one of {VIEWS}")
    try:
        jobs = rgjobs.listing(pid)
    except rgclient.Offline as err:
        return {"tab": "resumes", "rq": view, "online": False, "message": str(err), "jobs": [], "total": 0,
                "counts": {}, **progress(conn, pid, now)}
    gone = {j["id"] for j in sweep(conn, pid, jobs)}
    jobs = [j for j in jobs if j["id"] not in gone]
    views = classify(conn, pid, jobs, now)
    feed = api.cards_for(conn, pid, [k for k, *_ in views[view] if not k.startswith("app:")], now)
    pos = positions(jobs)
    cards = daygroups.stamp([{**(feed.get(key) or _outside_card(key, job, app)), "rq": _rq(job, app, view, closed, pos)}
                             for key, job, app, closed in views[view]], now, keep_order=view == "queue")
    return {"tab": "resumes", "rq": view, "online": True, "jobs": cards, "total": len(cards),
            "counts": counts(views), "cancelled_closed": len(gone), **progress(conn, pid, now)}


def summary(conn, pid: str, jobs: list[dict], now: datetime) -> dict:
    """The resume poll's extra (rgjobs.statuses): the tab's counts, the daily progress and the ids the §2c sweep
    just cancelled ('cancelled_closed')."""
    gone = [j["id"] for j in sweep(conn, pid, jobs)]
    views = classify(conn, pid, [j for j in jobs if j["id"] not in gone], now)
    return {"counts": counts(views), "cancelled_closed": gone, **progress(conn, pid, now)}


def _archive_for(pid: str, uid: str) -> tuple[list[int], list[int]]:
    """Archive the job's resumes in resumegen (a queued or running one is cancelled first): (archived, cancelled).
    resumegen offline or refusing: logged, whatever was done so far is returned."""
    done, stopped = [], []
    try:
        for job in rgjobs.listing(pid):
            if job.get("source_job_id") != uid or job.get("archived_at"):
                continue
            if job.get("status") in RUNNING or job.get("status") in WAITING:
                rgjobs.action(pid, job["id"], "cancel")
                stopped.append(job["id"])
            rgclient.request("POST", f"/api/jobs/{int(job['id'])}/archive", {})
            done.append(job["id"])
    except (rgclient.Offline, KeyError, ValueError) as err:
        log.warning("%s: its resumes could not be archived in resumegen: %s", uid, err)
    return done, [i for i in stopped if i in done]


def archive_resumes(conn, pid: str, uid: str, removed_id: int) -> list[int]:
    """GOAL20 (user 23:37): × Remove on a job archives its resumes in resumegen too (and Undo queues a cancelled one
    again), so both apps show the same; the ids go with the removal for Undo."""
    done, stopped = _archive_for(pid, uid)
    if done:
        removal.note_resumes(conn, removed_id, done, stopped)
    return done


TRIAGED_KEY = "rg.triaged.{}.{}"            # person, job uid -> {"rg": ids, "rg_requeue": ids} archived by Not interested


def follow_triage(conn, pid: str, uid: str, state: str) -> list[int]:
    """Not interested archives the job's resumes in resumegen like × Remove (job-hunt-agent's Ready already hid
    them); the job coming back (Undo, Restore, Save) restores the ones it archived. Returns the resumegen ids moved."""
    key = TRIAGED_KEY.format(pid, uid)
    if state == "dismissed":
        done, stopped = _archive_for(pid, uid)
        if done:
            with tx(conn):
                gstate.put(conn, key, {"rg": done, "rg_requeue": stopped})
        return done
    kept = gstate.get_json(conn, key)
    if not kept:
        return []
    back = restore_resumes(kept["rg"], kept.get("rg_requeue", []))
    if back:                                     # resumegen offline: kept, so the next Save / Restore tries again
        with tx(conn):
            gstate.put(conn, key, None)
    return back


def restore_resumes(rg_ids: list[int], requeue: list[int] = ()) -> list[int]:
    """Undo of a removal: its resumes come back in resumegen; the ones the removal cancelled go back in the queue."""
    if not rg_ids:
        return []
    kept, back = [int(i) for i in rg_ids if i not in requeue], []
    try:
        for i in requeue:
            try:
                rgclient.request("POST", f"/api/jobs/{int(i)}/retry", {})
                back.append(int(i))
            except ValueError:                       # still stopping (it was running): restored as cancelled
                kept.append(int(i))
        if kept:
            back += rgclient.request("POST", "/api/unarchive", {"ids": kept}).get("restored") or []
    except (rgclient.Offline, KeyError, ValueError) as err:
        log.warning("undo: resumes %s could not be restored in resumegen: %s", rg_ids, err)
    return back

