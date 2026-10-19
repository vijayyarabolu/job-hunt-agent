"""Settings > Setup: every account and key the app can use, what state it is in, and a Test for each.

state is one of "connected" (saved and, if tested, working), "missing" (not set up yet), "failing" (the last test
or call failed) or "demo" (demo mode answers with fixtures). Every item also says what still works without it, so a
missing piece is a message on this page, never a crash somewhere else.

Tests never spend credits: Anthropic lists one model, Apollo calls auth/health, Hunter reads the free account info,
Google refreshes the token and asks who signed in, Telegram calls getMe, the resume tailor lists its jobs.
"""
import logging
import threading
import time

from . import claude_api, config, rgclient, secrets_store
from .google import GoogleError, accounts, auth, setup as gsetup

log = logging.getLogger(__name__)
_LAST: dict[str, dict] = {}          # item id -> {"ok", "detail", "at"} of the last Test this process ran
_LOCK = threading.Lock()

ABOUT = {
    "google_client": {
        "name": "Google OAuth client",
        "why": "Google does not allow open-source apps to share one client secret, so you create your own OAuth "
               "client in Google Cloud (free, about 10 minutes). The steps are below.",
        "without": "Everything except Sheets sync, Drive uploads and Gmail reading."},
    "google": {
        "name": "Google account (Sheets and Drive)",
        "why": "A normal Google sign-in (OAuth with PKCE) on your own client. The app asks for two scopes: edit "
               "spreadsheets (your tracker sheet) and drive.file (only files this app creates).",
        "without": "The tracker works inside the app and exports to Excel; it just does not sync to a Google Sheet."},
    "anthropic": {
        "name": "Anthropic API key (Claude)",
        "why": "Anthropic has no sign-in for third-party apps, so Claude needs an API key. Get one at "
               "platform.claude.com > Settings > API keys. Pay as you go: the app uses Claude Sonnet 5.5 at $2 per "
               "million input tokens and $10 per million output tokens. Tailoring 30 resumes and reading about 300 "
               "emails a month comes to roughly $2 to $5.",
        "link": claude_api.KEY_PAGE,
        "without": "Job search, ranking, tracker and copy-paste prompts all work. Built-in resume tailoring, email "
                   "reading and one-click outreach drafts need it."},
    "apollo": {
        "name": "Apollo.io API key",
        "why": "Apollo has no OAuth for personal use; its API takes a key. Create one under Settings > Integrations "
               "> API in Apollo. The free plan gives a small monthly credit allowance (about 75 at the time of "
               "writing); this app spends 1 credit per company lookup and shows the cost before every call.",
        "link": "https://app.apollo.io/#/settings/integrations/api",
        "without": "Find contacts still reads the job post and the company's team page, and builds email guesses."},
    "hunter": {
        "name": "Hunter.io API key",
        "why": "Hunter's API takes a key (there is no OAuth). Copy it from hunter.io > API. The free plan includes "
               "50 credits a month at the time of writing; one domain search costs at most 1 credit here.",
        "link": "https://hunter.io/api-keys",
        "without": "Contacts come from Apollo, the job post and the team page instead."},
    "resume_tailor": {
        "name": "Resume tailoring",
        "why": "Built in: Claude rewrites your summary, skills and bullets for one job, using only facts from your "
               "own resume, and you download a .docx. Or point the app at your own resume-tailoring service "
               "(URL and token below) that speaks the same small HTTP API.",
        "without": "Copy JD + keywords still puts the job and the skills you are missing on the clipboard."},
    "telegram": {
        "name": "Telegram notifications (optional)",
        "why": "A bot token from @BotFather and your chat id. Free.",
        "link": "https://core.telegram.org/bots#how-do-i-create-a-bot",
        "without": "New matches show in the app (and as macOS notifications on a Mac)."},
}


def _last(item: str) -> dict | None:
    return _LAST.get(item)


def _state(item: str, saved: bool) -> str:
    if config.demo_mode():
        return "demo"
    if not saved:
        return "missing"
    last = _last(item)
    return "failing" if last and not last["ok"] else "connected"


def _google_item(port: int | None) -> dict:
    info = accounts.main_status()
    state = "missing" if not info["connected"] else _state("google", True)
    if info["lapsed"]:
        state = "failing"
    if config.demo_mode():
        state = "demo"
    detail = (f"Connected as {info['email']}" if info["connected"] and info.get("email") else
              "Connected" if info["connected"] else "The sign-in lapsed or was revoked" if info["lapsed"] else
              "Not connected")
    return {"id": "google", "state": state, "detail": detail, "email": info.get("email"),
            "can_connect": info["configured"],
            "redirect_uri": f"http://127.0.0.1:{port}/" if port else None}


def items(pid: str | None, port: int | None = None) -> list[dict]:
    """Every Setup item for the Settings page, in the order it shows them."""
    client_saved = auth.client_path().exists()
    try:
        client_id = auth.client()["client_id"] if client_saved else None
    except GoogleError:
        client_id = None
    key = secrets_store.get("anthropic_api_key")
    tailor_url = str(config.settings().get("resume_tailor_url") or "").strip()
    from .finder import keys as finder_keys
    apollo = finder_keys.key("apollo", pid) if pid else secrets_store.get("apollo_api_key")
    hunter = finder_keys.key("hunter", pid) if pid else secrets_store.get("hunter_api_key")
    tg = secrets_store.get("telegram_token")
    out = [
        {"id": "google_client", "state": "demo" if config.demo_mode() else "connected" if client_id else "missing",
         "detail": f"Client {client_id[:12]}… saved" if client_id else "No client saved yet",
         "steps": [{"text": t, "link": link} for t, link in gsetup.STEPS]},
        _google_item(port),
        {"id": "anthropic", "state": _state("anthropic", bool(key)), "detail": secrets_store.masked(key) or "No key",
         "model": claude_api.model()},
        {"id": "apollo", "state": _state("apollo", bool(apollo)), "detail": secrets_store.masked(apollo) or "No key"},
        {"id": "hunter", "state": _state("hunter", bool(hunter)), "detail": secrets_store.masked(hunter) or "No key"},
        {"id": "resume_tailor", "state": _state("resume_tailor", bool(tailor_url or key)),
         "detail": ("Built in; demo mode tailors without Claude" if config.demo_mode() else
                    f"Your service at {tailor_url}" if tailor_url else
                    "Built in, with your Anthropic key" if key else "Needs an Anthropic key, or a service URL"),
         "url": tailor_url, "token": secrets_store.masked(secrets_store.get("resume_tailor_token"))},
        {"id": "telegram", "state": "optional" if not tg else _state("telegram", True),
         "detail": secrets_store.masked(tg) or "Not set"},
    ]
    for entry in out:
        entry.update({k: v for k, v in ABOUT[entry["id"]].items()})
        last = _last(entry["id"])
        if last:
            entry["last_test"] = last
    return out


def _test_google() -> tuple[bool, str]:
    token = auth.access_token(force=True)
    return True, f"Google answered; signed in as {accounts._email_of(token)}"


def _test_apollo(pid: str | None) -> tuple[bool, str]:
    from .finder import apollo, keys
    key = keys.key("apollo", pid or "")
    if not key:
        return False, "no Apollo key saved"
    apollo.health(key)
    return True, "Apollo accepted the key (auth/health, 0 credits)"


def _test_hunter(pid: str | None) -> tuple[bool, str]:
    from .finder import hunter, keys
    key = keys.key("hunter", pid or "")
    if not key:
        return False, "no Hunter key saved"
    info = hunter.account(pid or "_setup", key, fresh=True)
    if info.get("error"):
        return False, info["error"]
    left = info.get("searches_left", info.get("credits_left"))
    return True, "Hunter accepted the key" + (f"; {left} credits left this month" if left is not None else "")


def _test_tailor() -> tuple[bool, str]:
    url = str(config.settings().get("resume_tailor_url") or "").strip()
    if not url:
        return claude_api.test() if claude_api.configured() else (False, "add an Anthropic key or a service URL")
    rgclient.request("GET", "/api/jobs?profile=_setup_test&lean=1")
    return True, f"{url} answered"


def _test_telegram() -> tuple[bool, str]:
    token = secrets_store.get("telegram_token")
    if not token:
        return False, "no bot token saved"
    from .finder import http
    got = http.call("telegram", "GET", f"https://api.telegram.org/bot{token}/getMe", {})
    return bool(got.get("ok")), f"bot @{(got.get('result') or {}).get('username', '?')}"


def test(item: str, pid: str | None) -> dict:
    """Run one item's Test; the answer is remembered so the status panel shows "failing" until it passes."""
    if config.demo_mode():
        result = {"ok": True, "detail": "demo mode: nothing was sent anywhere"}
    else:
        runs = {"google": _test_google, "anthropic": claude_api.test, "apollo": lambda: _test_apollo(pid),
                "hunter": lambda: _test_hunter(pid), "resume_tailor": _test_tailor, "telegram": _test_telegram,
                "google_client": lambda: (bool(auth.client()["client_id"]), "the client file reads fine")}
        if item not in runs:
            raise ValueError(f"nothing to test called {item!r}")
        try:
            ok, detail = runs[item]()
        except Exception as err:                               # every failure becomes a message on the page
            log.info("setup test %s failed: %s", item, err)
            ok, detail = False, str(err)[:300] or type(err).__name__
        result = {"ok": bool(ok), "detail": detail}
    result["at"] = time.time()
    with _LOCK:
        _LAST[item] = result
    return result


def save_key(name: str, value: str | None) -> dict:
    """A key from the Setup form; an empty value removes it. The old test result no longer applies."""
    item = {"anthropic_api_key": "anthropic", "apollo_api_key": "apollo", "hunter_api_key": "hunter",
            "telegram_token": "telegram", "telegram_chat": "telegram", "resume_tailor_token": "resume_tailor"}
    if name not in item:
        raise ValueError(f"unknown key {name!r}")
    secrets_store.put(name, value)
    with _LOCK:
        _LAST.pop(item[name], None)
    return {"saved": bool((value or "").strip())}


def save_tailor_url(url: str | None) -> dict:
    text = (url or "").strip().rstrip("/")
    if text and not text.startswith(("http://", "https://")):
        raise ValueError("the service URL starts with http:// or https://")
    secrets_store.save_setting("resume_tailor_url", text)
    with _LOCK:
        _LAST.pop("resume_tailor", None)
    return {"url": text}


def save_google_client(client_id: str | None, client_secret: str | None, json_text: str | None) -> dict:
    if json_text:
        client_id, client_secret = auth.parse_client_json(json_text)
    out = auth.save_client(client_id or "", client_secret or "")
    with _LOCK:
        _LAST.pop("google_client", None)
    return out
