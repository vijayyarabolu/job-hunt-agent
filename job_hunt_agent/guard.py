"""Pure completeness/drift gate for one board fetch (decision 001 §4). Only 'ok' runs may count misses."""

DRIFT_PARSE_ERROR_RATE = 0.05
PARTIAL_RATIO = 0.5
PARTIALS_BEFORE_ACCEPT = 2


def classify(http_ok: bool, n_raw: int, n_jobs: int, n_parse_err: int,
             last_ok_seen: int, prior_statuses: list[str]) -> str:
    """Return ok | partial | drift | http_error. prior_statuses is newest-first for this board."""
    if not http_ok:
        return "http_error"
    if n_raw and (n_parse_err / n_raw > DRIFT_PARSE_ERROR_RATE or n_jobs == 0):
        return "drift"
    if last_ok_seen and n_jobs < PARTIAL_RATIO * last_ok_seen:
        recent = prior_statuses[:PARTIALS_BEFORE_ACCEPT]
        accepted = len(recent) == PARTIALS_BEFORE_ACCEPT and all(s == "partial" for s in recent)
        return "ok" if accepted else "partial"
    return "ok"
