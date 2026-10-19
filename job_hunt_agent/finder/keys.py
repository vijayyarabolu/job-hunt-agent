"""Provider keys for the contact finder, from <home>/config/secrets.yaml (Settings > Setup saves them there). A
person's own key (`apollo: {<profile>_api_key: ...}`) wins over the shared one (`apollo_api_key`). A key only ever
goes into a request header: it is never returned, printed, logged or stored anywhere else."""
from .. import secrets_store

PROVIDERS = ("apollo", "hunter")


def key(provider: str, pid: str) -> str | None:
    """The person's key for the provider, else the shared one, or None when neither is set."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    own = (secrets_store.load().get(provider) or {})
    value = own.get(f"{pid}_api_key") if isinstance(own, dict) else None
    text = str(value).strip() if value else ""
    return text or secrets_store.get(f"{provider}_api_key")
