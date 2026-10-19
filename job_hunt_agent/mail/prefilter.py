"""which emails are worth Claude's time, decided in Python (no AI).

Skipped first, whatever else they say: job-alert / digest senders and subjects (LinkedIn / Indeed / Glassdoor /
ZipRecruiter alerts, "30+ new jobs for ..."), newsletters and promotions, and one-time codes ("Your verification
code", "Verify your account"). Then kept only when the email may be about an application: it comes from an ATS or a
screening / scheduling vendor, names a company / board tenant / requisition id the person applied to (match.py), or
its subject has job words. Everything else is skipped. Every skip is logged with its sender and subject
(data/logs/mail_skipped.log) so a miss can be spotted and the rules widened.
"""
import re
from email.utils import parseaddr

from .. import config, db
from .match import Match, is_ats

ALERT_SENDER = re.compile(r"(?i)^(?:jobalerts?-noreply@linkedin\.com|jobs-listings@linkedin\.com|"
                          r"[\w.+-]*alert[\w.+-]*@|[\w.+-]*digest[\w.+-]*@|[\w.+-]*newsletter[\w.+-]*@|"
                          r"(?:jobs?|job-?alerts?|noreply-jobs|recommendations?)@(?:\w+\.)?(?:indeed|glassdoor|"
                          r"ziprecruiter|dice|monster|simplyhired)\.com$|"
                          r"[\w.+-]*@(?:\w+\.)?(?:substack|beehiiv|mailchimp)\.com$|"
                          r"(?:marketing|promo(?:tions?)?|offers|deals|news|updates)@)")
ALERT_SUBJECT = re.compile(r"(?i)\bjob alert|\bnew jobs?\b|\bjobs? (?:for you|you may (?:be interested in|like)|"
                           r"matching|near you)|\brecommended (?:jobs|for you)\b|\bis hiring\b|"
                           r"\b\d+\+? (?:new )?(?:jobs|roles|openings)\b|\bnewsletter\b|\bweekly (?:digest|roundup)\b|"
                           r"\bwebinar\b|\b\d+% off\b")
CODE_SUBJECT = re.compile(r"(?i)\b(?:verification|security|one[- ]time|login|sign[- ]in|access) (?:code|pin)\b|"
                          r"\bverify your (?:email|e-mail|account|identity|candidate account)\b|\bpasscode\b|"
                          r"\b(?:OTP|2FA)\b|\bconfirm your (?:email|e-mail) address\b|\breset your password\b")
VENDOR_SENDER = re.compile(r"(?i)@(?:[\w-]+\.)*(?:hackerrank(?:forwork)?|codesignal|karat|codility|hirevue|coderpad|"
                           r"testgorilla|goodtime|modernloop|calendly|prelude)\.(?:com|io|co)$")
JOB_SUBJECT = re.compile(r"(?i)\b(?:applica(?:tion|nt)s?|applied|applying|candida(?:te|cy)|interview\w*|position|"
                         r"role|opportunit(?:y|ies)|offer|assessment|recruit\w*|hiring|talent|career|requisition|"
                         r"next steps?|thank(?:s| you) for (?:your )?(?:interest|applying)|update on your|screen\w*|"
                         r"job)\b")
SKIP_LOG = "mail_skipped.log"


def decide(sender: str, subject: str, found: Match) -> tuple[bool, str]:
    """(keep?, why)."""
    address = parseaddr(sender or "")[1].lower()
    subject = subject or ""
    if ALERT_SENDER.search(address) or ALERT_SUBJECT.search(subject):
        return False, "job alert / newsletter / promotion"
    if CODE_SUBJECT.search(subject):
        return False, "one-time code"
    if found.from_ats or VENDOR_SENDER.search(address) or is_ats(address.rpartition("@")[2]):
        return True, "ATS or screening vendor"
    if found.key is not None:
        return True, f"names {found.company}"
    if JOB_SUBJECT.search(subject):
        return True, "job words in the subject"
    return False, "not about an application"


def log_skip(pid: str, mail, why: str) -> None:
    """One line per skipped email (sender, subject, why), so misses can be spotted. data/ is never in git."""
    path = config.DATA / "logs" / SKIP_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    line = " | ".join(" ".join(str(x or "").split()) for x in (pid, db.iso(mail.sent_at), mail.sender, mail.subject, why))
    with path.open("a", encoding="utf-8") as out:
        out.write(f"{db.iso(db.utcnow())} {line}\n")
