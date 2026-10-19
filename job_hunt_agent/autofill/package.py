"""the extension as a download (Settings → "Get the Autofill extension") and its Chrome id.

The zip holds extension/ exactly as it is in the repo (no build step). manifest.json carries a fixed public "key",
so Chrome gives the unpacked extension the same id on every laptop: job-hunt-agent answers cross-origin requests from that
id only (settings.yaml `autofill_extension_ids` may add more).
"""
import base64
import hashlib
import io
import json
import zipfile
from functools import lru_cache
from pathlib import Path

from .. import config

EXTENSION = Path(__file__).resolve().parents[2] / "extension"
ZIP_NAME = "job-hunt-agent-autofill.zip"
SKIP_NAMES = (".DS_Store",)
SKIP_DIRS = ("test",)


def extension_id(public_key_b64: str) -> str:
    """Chrome's id for a manifest key: the first 128 bits of sha256(key), hex digits mapped to a-p."""
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


@lru_cache(maxsize=1)
def _manifest() -> dict:
    try:
        return json.loads((EXTENSION / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}


def own_id() -> str | None:
    key = _manifest().get("key")
    return extension_id(key) if key else None


def allowed_origins() -> frozenset[str]:
    ids = {i for i in [own_id(), *(config.settings().get("autofill_extension_ids") or [])] if i}
    return frozenset(f"chrome-extension://{i}" for i in ids)


def version() -> str:
    return str(_manifest().get("version") or "")


def zip_bytes() -> bytes:
    """extension/ as a zip whose top folder is job-hunt-agent-autofill/ (Load unpacked picks that folder)."""
    if not (EXTENSION / "manifest.json").is_file():
        raise KeyError("extension/manifest.json")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for path in sorted(EXTENSION.rglob("*")):
            rel = path.relative_to(EXTENSION)
            if path.is_file() and path.name not in SKIP_NAMES and not set(rel.parts) & set(SKIP_DIRS):
                out.write(path, Path("job-hunt-agent-autofill") / rel)
    return buf.getvalue()
