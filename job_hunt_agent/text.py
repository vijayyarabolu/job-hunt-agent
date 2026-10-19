"""Text helpers: HTML to plain text, normalization, boilerplate stripping, hashing."""
import hashlib
import html
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}
_SKIP_TAGS = {"script", "style"}
_SPACES = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")
_NON_WORD = re.compile(r"[^a-z0-9+#]+")
_BOILERPLATE = re.compile(
    r"(?i)\b(equal (employment )?opportunity|we are an equal|eeo statement|"
    r"reasonable accommodation|pay transparency|e-verify|"
    r"privacy (notice|policy)|applicants? (with|requiring) disabilit)"
)


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n- " if tag == "li" else "\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str | None, double_escaped: bool = False) -> str:
    """Convert HTML (optionally entity-escaped once more, as Greenhouse sends it) to plain text."""
    if not markup:
        return ""
    if double_escaped:
        markup = html.unescape(markup)
    parser = _Extractor()
    parser.feed(markup)
    parser.close()
    text = _SPACES.sub(" ", "".join(parser.parts))
    lines = (line.strip() for line in text.splitlines())
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def norm(value: str | None) -> str:
    """Lowercase, keep alphanumerics plus '+' and '#', single spaces."""
    return _NON_WORD.sub(" ", (value or "").lower()).strip()


def strip_boilerplate(text: str) -> str:
    """Drop legal/EEO paragraphs (only those lines) so dealbreakers and keyword scoring see the real JD."""
    kept = [line for line in (text or "").splitlines() if not _BOILERPLATE.search(line)]
    return _BLANK_LINES.sub("\n\n", "\n".join(kept)).strip()


def sha1(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()
