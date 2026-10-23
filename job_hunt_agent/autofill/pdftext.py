"""GOAL27 §5: plain text as a one-font PDF (the cover letter a form wants as a file). No packages: PDF 1.4,
Helvetica 11 pt on US Letter with 1 inch margins, lines wrapped by the font's own character widths, paragraphs kept,
pages added as needed. Text outside Windows-1252 becomes '?'."""
import zlib

PAGE_W, PAGE_H = 612, 792
MARGIN = 72
SIZE = 11
LEADING = 15
# Helvetica advance widths (1/1000 em) for ASCII 32..126, from the standard Adobe font metrics
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
      556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
      722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556,
      556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
      260, 334, 584]


def _width(text: str) -> float:
    return sum(_W[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in text) * SIZE / 1000


def wrap(text: str, width: float = PAGE_W - 2 * MARGIN) -> list[str]:
    """Lines that fit `width` points; a blank line between paragraphs is kept."""
    out: list[str] = []
    for para in str(text or "").replace("\r\n", "\n").split("\n"):
        words, line = para.split(), ""
        if not words:
            out.append("")
            continue
        for word in words:
            trial = f"{line} {word}".strip()
            if line and _width(trial) > width:
                out.append(line)
                line = word
            else:
                line = trial
        out.append(line)
    return out


def _escape(line: str) -> bytes:
    raw = line.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def render(text: str, title: str = "") -> bytes:
    """The PDF bytes."""
    lines = wrap(text)
    per_page = int((PAGE_H - 2 * MARGIN) // LEADING)
    pages = [lines[i:i + per_page] for i in range(0, max(len(lines), 1), per_page)] or [[]]
    objs: list[bytes] = []                                  # object n is objs[n - 1]
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"")                                        # pages: filled in below
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids = []
    for page in pages:
        body = [b"BT", f"/F1 {SIZE} Tf {LEADING} TL {MARGIN} {PAGE_H - MARGIN - SIZE} Td".encode()]
        for line in page:
            body.append(b"(" + _escape(line) + b") Tj T*")
        body.append(b"ET")
        stream = zlib.compress(b"\n".join(body))
        objs.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] /Contents {content} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>".encode())
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    info = b"<< /Title (" + _escape(title or "Cover letter") + b") /Producer (job-hunt-agent) >>"
    objs.append(info)
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, len(objs), xref)
    return bytes(out)
