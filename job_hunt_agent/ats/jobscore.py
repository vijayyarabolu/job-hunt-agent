"""JobScore career sites (`careers.jobscore.com/careers/<company>`, GOAL16 Tier A: US SMB tech). The public feed
`jobs/<company>/feed.json` is every open job with its full description, one request for the whole board, so an
absent job counts as a miss. robots.txt disallows only /apply_flow/ (checked 2026-10-08). JobScore asks feed readers
to poll at most once an hour, so its boards never run on the 30-minute tier (tiers.MIN_TIER).

Key '<company>' (the company code). opened_date is ISO (exact).
"""
from ..net import HttpError, get_json
from ..text import html_to_text
from . import fulllist
from .common import Ctx, FetchResult, SchemaDrift, make_job, parse_iso, require_list, schema_failure

LIMIT = 500


def list_url(key: str) -> str:
    return f"https://careers.jobscore.com/jobs/{key}/feed.json?sort=date&limit={LIMIT}"


def feed(key: str) -> dict:
    payload = get_json(list_url(key))
    require_list(payload, "jobs")
    return payload


def jobs(payload: dict) -> list[dict]:
    return [j for j in payload["jobs"] if isinstance(j, dict) and j.get("id")]


def _workplace(job: dict) -> str | None:
    remote = str(job.get("remote") or "").lower()
    return "remote" if remote.startswith("yes") else None


def _normalize(board: dict, job: dict, _detail) -> dict:
    url = job.get("detail_url") or f"https://careers.jobscore.com/careers/{board['key']}/jobs/{job.get('url_slug')}"
    when = parse_iso(job.get("opened_date") or job.get("created_on"))
    return make_job(
        ats="jobscore", key=board["key"], ext_id=job["id"], company=board["company"], title=job.get("title") or "",
        location=job.get("location") or "", country=job.get("country") or None, workplace=_workplace(job), url=url,
        apply_url=job.get("apply_url") or url, team=job.get("department"), posted_at=when,
        precision="exact" if when else "unknown", description=html_to_text(job.get("description")),
        employment=job.get("job_type"),
    )


def fetch(board: dict, ctx: Ctx) -> FetchResult:
    try:
        listed = jobs(feed(board["key"]))
    except HttpError as err:
        return FetchResult([], err.status, error=str(err))
    except SchemaDrift as err:
        return schema_failure(err)
    return fulllist.run(board, ctx, listed, id_of=lambda j: str(j["id"]), title_of=lambda j: j.get("title") or "",
                        normalize=_normalize, detail=None, truncated=len(listed) >= LIMIT)


def survey(key: str) -> dict:
    payload = feed(key)
    listed = jobs(payload)
    return {"n_jobs": len(listed), "name": payload.get("company_name"),
            "rows": [(parse_iso(j.get("opened_date")), j.get("location") or "", j.get("country") or None,
                      j.get("title") or "") for j in listed]}
