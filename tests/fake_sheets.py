"""In-memory Google Sheets model for tests/fake_google.py: grids, A1 ranges, the batchUpdate requests job-hunt-agent sends,
values batchGet/batchUpdate/append. Cells are {'v', 'formula', 'link', 'note', 'fmt'}; shown text follows Sheets'
FORMATTED_VALUE (a DATE-formatted serial shows as MM/DD/YYYY, formulas are not evaluated and show '')."""
import re
from datetime import date, timedelta

EPOCH = date(1899, 12, 30)
_A1 = re.compile(r"^(?:'((?:[^']|'')+)'|([^'!]+))!?(.*)$")
_END = re.compile(r"^([A-Z]*)(\d*)$")


def col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def parse_range(a1: str) -> tuple[str, int, int, int | None, int | None]:
    """'Tab'!O2:O -> (title, row0, col0, row_end, col_end); ends exclusive, None = open. A bare 'A1' (no tab)
    means the first sheet, as in the real API (title "")."""
    if "!" not in a1 and _END.match(a1.partition(":")[0]):
        title, ref = "", a1
    else:
        match = _A1.match(a1)
        title = (match.group(1) or "").replace("''", "'") or match.group(2)
        ref = match.group(3) or ""
    if not ref:
        return title, 0, 0, None, None
    first, _, last = ref.partition(":")
    c0, r0 = _END.match(first).groups()
    c1, r1 = _END.match(last or first).groups()
    return (title, int(r0) - 1 if r0 else 0, col_index(c0) if c0 else 0,
            int(r1) if r1 else None, col_index(c1) + 1 if c1 else None)


CHIP_NAMES: dict[str, str] = {}          # Drive link -> file name (fake_google files): what a chip shows
CHIP_OWNERS: dict[str, str] = {}         # Drive link -> the account that owns the file (fake_google files)
SHARED_BY = "owner@example.com"          # the owner shares the person's folder with them; the person shares nothing
CALLER: str | None = None                # the account of the request being served (fake_google sets it)


def _check_chips(chips: list[dict]) -> None:
    """GOAL11 §5, as Sheets does it: a chip is built from the file, so the writer must be able to see it (a file in
    the person's own Drive is not visible to the owner's token)."""
    for run in chips or []:
        owner = CHIP_OWNERS.get(((run.get("chip") or {}).get("richLinkProperties") or {}).get("uri"))
        if owner and CALLER and owner not in (CALLER, SHARED_BY):
            raise ValueError("Error fetching chip data: Permission denied")


def _chip_text(text: str, chips: list[dict]) -> tuple[str, list[dict]]:
    """A chip cell as Sheets reads it back: each '@' a chip points at shows the file's name, and the chip runs cover
    the names (BMP text only)."""
    out, last, runs = "", 0, []
    for run in chips:
        uri = ((run.get("chip") or {}).get("richLinkProperties") or {}).get("uri")
        if uri is None:
            continue
        at = run.get("startIndex", 0)
        out += text[last:at]
        runs.append({"startIndex": len(out), "chip": run["chip"]})
        out += CHIP_NAMES.get(uri, "@")
        runs.append({"startIndex": len(out)})
        last = at + 1
    out += text[last:]
    return out, [r for r in runs if r.get("chip") or r["startIndex"] < len(out)]


def shown(cell: dict | None) -> str:
    if not cell or cell.get("formula"):
        return ""
    value = cell.get("v")
    if value is None:
        return ""
    if cell.get("fmt") == "DATE" and isinstance(value, (int, float)):
        return (EPOCH + timedelta(days=int(value))).strftime("%m/%d/%Y")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def wrapped_writes(book: "Spreadsheet") -> list[tuple]:
    """Value writes under the header row into a visible column that would grow the row: text with a line break, or
    a cell not set to wrap CLIP (user, 2026-10-06 evening: every cell the app writes is one clipped line)."""
    bad = []
    for title, row, col, cell, fields in book.writes:
        entered = cell.get("userEnteredValue")
        if row == 0 or entered is None or col in book.sheet(title)["hidden"]:
            continue
        text = entered.get("stringValue") or ""
        clipped = (cell.get("userEnteredFormat") or {}).get("wrapStrategy") == "CLIP" and (
            "wrapStrategy" in fields or fields == "*")
        if "\n" in text or "\r" in text or not clipped:
            bad.append((title, row + 1, col + 1, text[:60], fields))
    return bad


class Spreadsheet:
    def __init__(self, sid: str, title: str):
        self.sid, self.title, self.next_id = sid, title, 1
        self.writes: list[tuple[str, int, int, dict, str]] = []     # every updateCells cell: (tab, row, col, cell, fields)
        self.sheets: list[dict] = []
        self.metadata: list[dict] = []
        self.add_sheet({"title": "Sheet1"})

    # ---- model helpers (tests use these to play the phone) ----
    def sheet(self, title: str | None = None, sheet_id: int | None = None) -> dict:
        for sheet in self.sheets:
            if sheet["properties"]["title"] == title or sheet["properties"]["sheetId"] == sheet_id:
                return sheet
        if title == "" and self.sheets:
            return self.sheets[0]
        raise KeyError(title or sheet_id)

    def add_sheet(self, props: dict) -> dict:
        grid = {"rowCount": 1000, "columnCount": 26, **props.get("gridProperties", {})}
        props = {"sheetId": props.get("sheetId", self.next_id), "index": len(self.sheets), **props,
                 "gridProperties": grid}
        self.next_id = max(self.next_id, props["sheetId"]) + 1
        sheet = {"properties": props, "grid": [], "conditionalFormats": [], "protectedRanges": [],
                 "basicFilter": None, "validation": {}, "hidden": set()}
        self.sheets.append(sheet)
        return sheet

    def row_values(self, title: str) -> list[list[str]]:
        return [[shown(c) for c in row] for row in self.sheet(title)["grid"]]

    def cell(self, title: str, row: int, col: int) -> dict:
        grid = self.sheet(title)["grid"]
        while len(grid) <= row:
            grid.append([])
        while len(grid[row]) <= col:
            grid[row].append({})
        return grid[row][col]

    # ---- requests ----
    def to_json(self) -> dict:
        sheets = [{"properties": s["properties"], "conditionalFormats": s["conditionalFormats"],
                   "protectedRanges": s["protectedRanges"], **({"basicFilter": s["basicFilter"]}
                                                                if s["basicFilter"] else {})} for s in self.sheets]
        return {"spreadsheetId": self.sid, "properties": {"title": self.title}, "sheets": sheets,
                "developerMetadata": self.metadata,
                "spreadsheetUrl": f"https://docs.google.com/spreadsheets/d/{self.sid}/edit"}

    def batch_update(self, requests: list[dict]) -> list[dict]:
        return [self._one(kind, body) for req in requests for kind, body in req.items()]

    def _one(self, kind: str, body: dict) -> dict:
        if kind == "addSheet":
            return {"addSheet": {"properties": self.add_sheet(dict(body["properties"]))["properties"]}}
        if kind == "updateCells":
            self._update_cells(body)
        elif kind == "appendCells":
            grid = self.sheet(sheet_id=body["sheetId"])["grid"]
            last = max((i for i, r in enumerate(grid) if any(shown(c) or c.get("formula") for c in r)), default=-1)
            del grid[last + 1:]
            for row in body.get("rows", []):
                grid.append([self._cell({}, v, body.get("fields", "*")) for v in row.get("values", [])])
        elif kind == "updateSheetProperties":
            sheet = self.sheet(sheet_id=body["properties"]["sheetId"])
            grid = {**sheet["properties"]["gridProperties"], **body["properties"].get("gridProperties", {})}
            sheet["properties"] = {**sheet["properties"], **body["properties"], "gridProperties": grid}
        elif kind == "setBasicFilter":
            self.sheet(sheet_id=body["filter"]["range"]["sheetId"])["basicFilter"] = body["filter"]
        elif kind == "setDataValidation":
            rng = body["range"]
            self.sheet(sheet_id=rng["sheetId"])["validation"][rng.get("startColumnIndex", 0)] = body.get("rule")
        elif kind == "addConditionalFormatRule":
            self.sheet(sheet_id=body["rule"]["ranges"][0]["sheetId"])["conditionalFormats"].insert(
                body.get("index", 0), body["rule"])
        elif kind == "deleteConditionalFormatRule":
            del self.sheet(sheet_id=body["sheetId"])["conditionalFormats"][body["index"]]
        elif kind == "addProtectedRange":
            rng = {**body["protectedRange"], "protectedRangeId": self.next_id}
            self.next_id += 1
            self.sheet(sheet_id=rng["range"]["sheetId"])["protectedRanges"].append(rng)
        elif kind == "deleteProtectedRange":
            for sheet in self.sheets:
                sheet["protectedRanges"] = [p for p in sheet["protectedRanges"]
                                            if p["protectedRangeId"] != body["protectedRangeId"]]
        elif kind == "insertDimension":
            self._insert(body["range"])
        elif kind == "deleteDimension" and body["range"]["dimension"] == "ROWS":       # a row deleted
            rng = body["range"]
            sheet = self.sheet(sheet_id=rng["sheetId"])
            del sheet["grid"][rng["startIndex"]:rng["endIndex"]]
            sheet["properties"]["gridProperties"]["rowCount"] -= rng["endIndex"] - rng["startIndex"]
        elif kind == "appendDimension":
            grid = self.sheet(sheet_id=body["sheetId"])["properties"]["gridProperties"]
            grid["columnCount" if body["dimension"] == "COLUMNS" else "rowCount"] += body["length"]
        elif kind == "updateDimensionProperties":
            rng = body["range"]
            sheet = self.sheet(sheet_id=rng["sheetId"])
            for i in range(rng["startIndex"], rng["endIndex"]):
                if "hiddenByUser" in body["fields"] and rng["dimension"] == "COLUMNS":
                    (sheet["hidden"].add if body["properties"].get("hiddenByUser") else sheet["hidden"].discard)(i)
                if "pixelSize" in body["fields"] and rng["dimension"] == "ROWS":
                    sheet.setdefault("heights", {})[i] = body["properties"]["pixelSize"]
        elif kind == "repeatCell" and "wrapStrategy" in body.get("fields", ""):
            rng = body["range"]
            sheet = self.sheet(sheet_id=rng["sheetId"])
            wrap = body["cell"].get("userEnteredFormat", {}).get("wrapStrategy")
            for r in range(rng.get("startRowIndex", 0), rng.get("endRowIndex", len(sheet["grid"]))):
                for c in range(rng.get("startColumnIndex", 0), rng.get("endColumnIndex", 0)):
                    self.cell(sheet["properties"]["title"], r, c)["wrap"] = wrap
        elif kind not in ("repeatCell", "autoResizeDimensions"):
            raise ValueError(f"fake sheets: unsupported request {kind}")
        return {}

    def _insert(self, rng: dict) -> None:
        """insertDimension: cells right of (below) the new columns (rows) move, as do dropdowns and hidden columns."""
        sheet = self.sheet(sheet_id=rng["sheetId"])
        at, n = rng["startIndex"], rng["endIndex"] - rng["startIndex"]
        grid = sheet["properties"]["gridProperties"]
        if rng["dimension"] == "COLUMNS":
            for row in sheet["grid"]:
                if len(row) > at:
                    row[at:at] = [{} for _ in range(n)]
            sheet["validation"] = {(c + n if c >= at else c): v for c, v in sheet["validation"].items()}
            sheet["hidden"] = {c + n if c >= at else c for c in sheet["hidden"]}
            grid["columnCount"] += n
        else:
            sheet["grid"][at:at] = [[] for _ in range(n)]
            grid["rowCount"] += n

    def _update_cells(self, body: dict) -> None:
        if "start" in body:
            sheet_id, r0, c0 = body["start"]["sheetId"], body["start"].get("rowIndex", 0), \
                body["start"].get("columnIndex", 0)
        else:
            rng = body["range"]
            sheet_id, r0, c0 = rng["sheetId"], rng.get("startRowIndex", 0), rng.get("startColumnIndex", 0)
        sheet = self.sheet(sheet_id=sheet_id)
        title, limits = sheet["properties"]["title"], sheet["properties"]["gridProperties"]
        for i, row in enumerate(body.get("rows", [])):
            for j, value in enumerate(row.get("values", [])):
                if r0 + i >= limits["rowCount"] or c0 + j >= limits["columnCount"]:
                    raise ValueError(f"Range ({title}!R{r0 + i + 1}C{c0 + j + 1}) exceeds grid limits")
                target = self.cell(title, r0 + i, c0 + j)
                target.update(self._cell(target, value, body.get("fields", "*")))
                self.writes.append((title, r0 + i, c0 + j, value, body.get("fields", "*")))

    @staticmethod
    def _cell(old: dict, value: dict, fields: str) -> dict:
        out = dict(old)
        entered = value.get("userEnteredValue")
        if entered is not None or "userEnteredValue" in fields or fields == "*":
            entered = entered or {}
            out["formula"] = entered.get("formulaValue")
            out["v"] = next((entered[k] for k in ("stringValue", "numberValue", "boolValue") if k in entered), None)
        if "textFormatRuns" in value or "textFormatRuns" in fields or fields == "*":
            runs = value.get("textFormatRuns") or []
            out["link"] = next((r["format"]["link"]["uri"] for r in runs if r.get("format", {}).get("link")), None)
            out["runs"] = runs or None
        if "note" in value or "note" in fields:
            out["note"] = value.get("note")
        wrap = (value.get("userEnteredFormat") or {}).get("wrapStrategy")
        if wrap or "wrapStrategy" in fields:
            out["wrap"] = wrap
        if "chipRuns" in value or "chipRuns" in fields:
            out["chips"] = value.get("chipRuns") or None
            _check_chips(out["chips"])
            if out["chips"] and isinstance(out.get("v"), str):
                out["v"], out["chips"] = _chip_text(out["v"], out["chips"])
        elif entered is not None or "userEnteredValue" in fields:
            out["chips"] = None                        # Sheets: a new value erases the cell's chips
        fmt = ((value.get("userEnteredFormat") or {}).get("numberFormat") or {}).get("type")
        if fmt or "numberFormat" in fields:
            out["fmt"] = fmt
        return out

    def raw_values(self, title: str) -> list[list]:
        """valueRenderOption=FORMULA: formulas as '=...', numbers as numbers (dates as serials), '' when empty."""
        return [["" if not c else c.get("formula") or ("" if c.get("v") is None else c["v"]) for c in row]
                for row in self.sheet(title)["grid"]]

    def batch_get(self, ranges: list[str], render: str = "FORMATTED_VALUE") -> list[dict]:
        out = []
        for a1 in ranges:
            title, r0, c0, r1, c1 = parse_range(a1)
            rows = self.raw_values(title) if render == "FORMULA" else self.row_values(title)
            values = [row[c0:c1] for row in rows[r0:r1]]
            values = [row[:max((i + 1 for i, v in enumerate(row) if v != ""), default=0)] for row in values]
            while values and not values[-1]:
                values.pop()
            out.append({"range": a1, "majorDimension": "ROWS", "values": values})
        return out

    def grid_data(self, a1) -> dict:
        """spreadsheets.get with one range or several (one data block each, row heights in rowMetadata)."""
        blocks = [self._grid_block(r) for r in ([a1] if isinstance(a1, str) else a1)]
        return {"sheets": [{"data": blocks}]}

    def _grid_block(self, a1: str) -> dict:
        """spreadsheets.get with ranges + fields rowData: each cell's value, shown text, link and rich-text runs
        (a cell-wide link, as an imported cell has, comes back as `hyperlink` with no runs, like Sheets). One cell
        ('E5') or a column range ('E2:E', to the last row with data)."""
        title, r0, c0, r1, c1 = parse_range(a1)
        grid = self.sheet(title)["grid"]
        end = r1 if r1 is not None else len(grid)
        rows = []
        last = c1 if c1 is not None else c0 + 1
        for r in range(r0, max(end, r0 + 1)):
            row = grid[r] if r < len(grid) else []
            rows.append({"values": [self._grid_value(row[c] if c < len(row) else {}) for c in range(c0, last)]})
        heights = self.sheet(title).get("heights", {})
        meta = [{"pixelSize": heights.get(r, 21)} for r in range(r0, max(end, r0 + 1))]
        return {"startRow": r0, "rowData": rows, "rowMetadata": meta}

    @staticmethod
    def _grid_value(cell: dict) -> dict:
        value = {}
        if cell.get("formula") or cell.get("v") is not None:
            entered = {"formulaValue": cell["formula"]} if cell.get("formula") else \
                {"numberValue" if isinstance(cell["v"], (int, float)) else "stringValue": cell["v"]}
            value = {"userEnteredValue": entered, "formattedValue": shown(cell)}
        if cell.get("runs"):
            value["textFormatRuns"] = [{k: v for k, v in r.items() if not (k == "startIndex" and v == 0)}
                                       for r in cell["runs"]]
        elif cell.get("link"):
            value["hyperlink"] = cell["link"]
        if cell.get("chips"):
            value["chipRuns"] = cell["chips"]
        if cell.get("wrap"):
            value["userEnteredFormat"] = {"wrapStrategy": cell["wrap"]}
        return value

    def values_update(self, a1: str, values: list[list], append: bool = False) -> None:
        title, r0, c0, _, _ = parse_range(a1)
        if append:
            r0 = len([r for r in self.sheet(title)["grid"] if any(shown(c) for c in r)])
        for i, row in enumerate(values):
            for j, value in enumerate(row):
                cell = self.cell(title, r0 + i, c0 + j)
                is_formula = isinstance(value, str) and value.startswith("=")
                cell.update({"v": None if is_formula else value, "formula": value if is_formula else None})
