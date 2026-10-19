"""Google setup in plain words: the numbered steps for creating your own OAuth client (shown on Settings > Setup and
by `job-hunt-agent google setup`), then the browser consent. Linking your existing tracker sheet is its own step
(`job-hunt-agent google link`), always previewed first. Plus `google selftest`: read-only checks of the token and
every linked sheet. Nothing here creates a spreadsheet.
"""
from .. import gsync, tracker_layout
from . import GoogleError, auth

CONSOLE = "https://console.cloud.google.com"
# (what to do, the page to do it on). Shared by the Settings page (api: /api/setup) and the terminal.
STEPS = (
    ("Create a Google Cloud project (free). Any name works, for example job-hunt-agent.",
     f"{CONSOLE}/projectcreate"),
    ("Turn on the Google Sheets API: open the page and press Enable.",
     f"{CONSOLE}/apis/library/sheets.googleapis.com"),
    ("Turn on the Google Drive API the same way (resume uploads go to a job-hunt-agent folder in your Drive).",
     f"{CONSOLE}/apis/library/drive.googleapis.com"),
    ("Optional, only for reading job emails: turn on the Gmail API too.",
     f"{CONSOLE}/apis/library/gmail.googleapis.com"),
    ("Branding: give the app a name (job-hunt-agent) and your own email as the support and developer contact. Save.",
     f"{CONSOLE}/auth/branding"),
    ("Audience: pick External, add your own Google address as a test user, then press Publish app so it reads "
     "In production. Personal, unverified apps with fewer than 100 users need no Google review, and their sign-in "
     "does not expire every 7 days the way Testing does.",
     f"{CONSOLE}/auth/audience"),
    ("Data access: add exactly these scopes: https://www.googleapis.com/auth/spreadsheets (to edit your tracker "
     "sheet) and https://www.googleapis.com/auth/drive.file (only files this app creates). For email reading add "
     "https://www.googleapis.com/auth/gmail.readonly as well.",
     f"{CONSOLE}/auth/scopes"),
    ("Clients: Create client, Application type Desktop app, name it job-hunt-agent, Create. Copy the client ID and "
     "client secret into the form below (or paste the downloaded JSON). A Desktop client accepts the app's "
     "http://127.0.0.1 redirect on any port, so there is nothing else to register.",
     f"{CONSOLE}/auth/clients"),
    ("Press Connect Google. Google warns that it hasn't verified this app: that is expected for your own client. "
     "Choose Advanced, then Go to job-hunt-agent, allow both boxes, and you are back here connected.", None),
)


def steps_text() -> str:
    return "\n".join(f" {i}. {text}" + (f"\n      {link}" if link else "") for i, (text, link) in enumerate(STEPS, 1))


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def wait_for_client() -> bool:
    while not auth.client_path().exists():
        if _ask(f"Press Enter once {auth.client_path()} is saved (q to stop): ").lower() == "q":
            return False
    auth.client()                                              # validates it, with a clear error if wrong
    return True


def setup(conn, open_browser=None) -> int:
    print("Connect Job Hunt Agent to Google (one time, free, about 10 minutes). The Settings page shows the same steps.")
    print(steps_text())
    print(f"\nInstead of the form, you can save the downloaded JSON as {auth.client_path()} (never commit it).")
    if not wait_for_client():
        return 2
    print("A browser window opens: sign in, allow access, then come back here.")
    auth.login(**({"open_browser": open_browser} if open_browser else {}))
    print("Connected. Next, link each profile's existing tracker sheet (a preview first; nothing changes until --yes):")
    print("  job-hunt-agent google link --profile <profile> '<sheet URL>'")
    return 0


def _check(target: gsync.Target, pending: set[str]) -> str:
    missing = [h for h in target.missing_columns() if h not in pending]
    problems = ([f"columns missing: {missing}"] if missing else []) + (
        [f"no '{gsync.KEY_HEADER}' column (link it again)"] if target.key_col is None else [])
    if problems:
        raise ValueError("; ".join(problems))
    waiting = [h for h in target.missing_columns() if h in pending]
    return (f"{len(target.synced)} columns synced, key in column {gsync.letter(target.key_col)}"
            + (f"; not added yet: {', '.join(waiting)} (job-hunt-agent google add-columns)" if waiting else ""))


def selftest(conn) -> list[tuple[str, bool, str]]:
    """Read-only: the token works and every linked sheet has its tabs, header row and key column."""
    steps = []

    def step(name: str, fn) -> None:
        try:
            steps.append((name, True, str(fn())))
        except (GoogleError, ValueError) as err:
            steps.append((name, False, str(err)))

    step("token", lambda: bool(auth.access_token()))
    linked = gsync.profiles_linked(conn)
    for pid in linked:
        pending = {h for h, _ in tracker_layout.of_profile(pid).added}
        try:
            found = gsync.targets(conn, pid)
        except GoogleError as err:
            steps.append((pid, False, str(err)))
            continue
        for target in found:
            step(f"{pid} / {target.title}", lambda t=target: _check(t, pending))
    if not linked:
        steps.append(("linked sheets", False, "none yet: job-hunt-agent google link --profile <p> <sheet url>"))
    return steps
