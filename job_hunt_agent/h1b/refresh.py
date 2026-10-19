"""GOAL24 §1 refresh: once a month (com.job_hunt_agent.h1b) look at the DOL performance page; when a new quarter's file is
there, download it (headless Chrome: dol.gov refuses plain HTTP clients) into data/h1b and load the last 8 quarters
again. Files that fell out of the window are deleted. Nothing changes when there is no new file."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import label, load

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
NODE_PLACES = ("/opt/homebrew/bin/node", "/usr/local/bin/node")
LIST_TIMEOUT_S = 180
DOWNLOAD_TIMEOUT_S = 3600
SCRIPT = Path(__file__).with_name("dolfetch.mjs")


class RefreshError(RuntimeError):
    pass


def node() -> str:
    found = shutil.which("node") or next((p for p in NODE_PLACES if Path(p).exists()), None)
    if not found:
        raise RefreshError("node is not installed (brew install node)")
    return found


def _run(mode: str, urls: list[str], timeout: int) -> dict:
    if not Path(CHROME).exists():
        raise RefreshError(f"Google Chrome is not at {CHROME}")
    out_dir = load.data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jha-dol-") as profile:
        done = subprocess.run([node(), str(SCRIPT), CHROME, str(Path(profile) / "chrome"), str(out_dir), mode, *urls],
                              capture_output=True, text=True, timeout=timeout, check=False)
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise RefreshError(f"dolfetch {mode} printed nothing: {done.stderr.strip()[-300:]}")
    result = json.loads(lines[-1])
    if result.get("error"):
        raise RefreshError(result["error"])
    return result


def links() -> list[str]:
    """Every LCA / PERM disclosure file the DOL page links to."""
    return _run("list", [], LIST_TIMEOUT_S)["links"]


def plan(found: list[str], have: set[str], loaded: set[str]) -> dict:
    """What a refresh does with the page's links, the files on disk and the files loaded (pure; tests check it):
    {names: {file: url} of the window, new: [url], remove: [file], load: bool}."""
    picked, _ = load.wanted(found)
    names = {u.rsplit("/", 1)[-1]: u for u in picked}
    new = [u for n, u in names.items() if n not in have]
    remove = sorted(n for n in have if load.parse_name(n) and n not in names)
    return {"names": names, "new": new, "remove": remove, "load": bool(new or remove or loaded != set(names))}


def refresh(conn, force: bool = False, log=print) -> dict:
    """{changed, downloaded, removed, load?}: download the new files of the window, load when anything changed."""
    folder = load.data_dir()
    todo = plan(links(), {p.name for p in folder.glob("*.xlsx")}, {r[0] for r in conn.execute("SELECT name FROM h1b_files")})
    if not todo["names"]:
        raise RefreshError("the DOL page lists no LCA disclosure file")
    downloaded = []
    if todo["new"]:
        log(f"downloading {len(todo['new'])} new file(s): {', '.join(u.rsplit('/', 1)[-1] for u in todo['new'])}")
        for item in _run("get", todo["new"], DOWNLOAD_TIMEOUT_S)["saved"]:
            if item.get("error"):
                raise RefreshError(f"{item['url']}: {item['error']}")
            downloaded.append(Path(item["file"]).name)
    for name in todo["remove"]:
        (folder / name).unlink(missing_ok=True)
    if not (todo["load"] or force):
        return {"changed": False, "downloaded": [], "removed": []}
    result = load.load_dir(conn, folder, todo["names"], log)
    label.forget()
    return {"changed": True, "downloaded": downloaded, "removed": todo["remove"], "load": result}
