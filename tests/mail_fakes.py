"""stand-ins for `claude -p` in the mail tests (no real Claude call is ever made by the suite).

rules_claude: a Claude that agrees with the rules (kind = the rules' answer, the one listed application when there is
exactly one, confidence high): for tests about reading, cursors and plumbing. claude_says(...): fixed answers by
subject. Both record each prompt so a test can check what was sent."""
import json

from job_hunt_agent.mail import classify


def items(prompt: str) -> list[dict]:
    """The emails sent in one prompt (the JSON lines after the header)."""
    body = prompt.split("Emails (JSON, one per line):\n", 1)[1]
    return [json.loads(line) for line in body.splitlines() if line.strip()]


class FakeClaude:
    def __init__(self, decide, stop_after: int | None = None, stop_kind: str = "limit"):
        self.decide, self.prompts, self.stop_after, self.stop_kind = decide, [], stop_after, stop_kind

    def __call__(self, prompt: str, pid: str):
        if self.stop_after is not None and len(self.prompts) >= self.stop_after:
            self.prompts.append(prompt)
            return None, self.stop_kind, {}
        self.prompts.append(prompt)
        results = [{"i": it["i"], **self.decide(it)} for it in items(prompt)]
        return {"results": results}, "ok", {"input_tokens": 600, "output_tokens": 50 * len(results)}


def _agree(it: dict) -> dict:
    verdict = classify.classify(it["subject"], it["text"], True)
    apps = [a["id"] for a in it["applications"]]
    return {"kind": verdict.kind or "other", "application": apps[0] if len(apps) == 1 else None,
            "candidates": apps, "confidence": "high", "reason": "rules oracle"}


def rules_claude(**kw) -> FakeClaude:
    return FakeClaude(_agree, **kw)


def claude_says(by_subject: dict, default: dict | None = None) -> FakeClaude:
    """{subject part: answer fields}; 'application' may be 'only' (the one listed) or an id."""
    def decide(it):
        found = next((a for part, a in by_subject.items() if part in it["subject"]), default or {"kind": "other"})
        apps = [a["id"] for a in it["applications"]]
        app = found.get("application", "only")
        app = (apps[0] if len(apps) == 1 else None) if app == "only" else app
        return {"kind": found["kind"], "application": app, "candidates": found.get("candidates", apps),
                "confidence": found.get("confidence", "high"), "reason": found.get("reason", "says so")}
    return FakeClaude(decide)
