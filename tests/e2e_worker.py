"""GOAL33 §5: plays resumegen's worker for one job of a COPY of resumegen's data (never the live one): claims it, then
finishes it with the files of an earlier finished run of the same person, as Needs you (a check failed) or Ready.
No Claude call. Run with resumegen's Python: e2e_worker.py <resumegen dir> <job id> <source job id> needs_you|ready"""
import json
import shutil
import sys
from pathlib import Path

LIVE = Path.home() / "Documents" / "resumegen"


def main(home: str, jid: int, src: int, status: str) -> str:
    sys.path.insert(0, home)
    from resumegen import config, db
    if config.DB_PATH.resolve().is_relative_to(LIVE.resolve()):
        raise SystemExit(f"refusing the live resumegen data: {config.DB_PATH}")
    conn = db.connect(setup=False)
    run = config.RUNS / str(jid)
    if not db.claim(conn, jid, str(run)):
        raise SystemExit(f"job {jid} is not queued")
    source = db.get_job(conn, src)
    shutil.copytree(source["run_dir"], run, dirs_exist_ok=True)
    result = json.loads(source["result"].replace(f"/runs/{src}/", f"/runs/{jid}/"))
    result.pop("accepted", None)
    if status == "ready":
        result["checks"] = {"ok": True, "failures": []}
    db.set_status(conn, jid, status, text=f"e2e: {status}", extra={"result": json.dumps(result)})
    return db.get_job(conn, jid)["status"]


if __name__ == "__main__":
    print(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]))
