"""One attempt per provider call (a retry could spend credits twice), a short timeout, JSON in and out. Errors carry
the status and the provider's own message, never the request headers (the key travels in a header)."""
import json
import urllib.error
import urllib.parse
import urllib.request

from .. import net

TIMEOUT_S = 20
MAX_BODY = 2_000_000
MAX_MESSAGE = 300


class ProviderError(Exception):
    def __init__(self, provider: str, status: int | None, message: str):
        super().__init__(f"{provider}: {f'HTTP {status}: ' if status else ''}{message}")
        self.provider, self.status, self.message = provider, status, message


def _message(err: urllib.error.HTTPError) -> str:
    try:
        data = json.loads(err.read(MAX_BODY) or b"{}")
    except (json.JSONDecodeError, OSError, ValueError):
        return err.reason or "error"
    if not isinstance(data, dict):
        return err.reason or "error"
    text = data.get("error") or data.get("message") or err.reason
    return str(text)[:MAX_MESSAGE]


def call(provider: str, method: str, url: str, headers: dict, params: dict | None = None,
         body: dict | None = None) -> dict:
    """The provider's JSON answer; raises ProviderError (no retry)."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    data = json.dumps(body).encode() if body is not None else None
    sent = {**headers, "Accept": "application/json", "Content-Type": "application/json", "User-Agent": net.user_agent()}
    req = urllib.request.Request(url, data=data, method=method, headers=sent)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            answer = json.loads(resp.read(MAX_BODY) or b"{}")
    except urllib.error.HTTPError as err:
        message = _message(err)
        err.close()
        raise ProviderError(provider, err.code, message) from None
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise ProviderError(provider, None, str(getattr(err, "reason", err))) from None
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise ProviderError(provider, 200, f"invalid JSON: {err}") from None
    if not isinstance(answer, dict):
        raise ProviderError(provider, 200, "unexpected answer")
    return answer
