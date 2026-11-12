"""Pull every table out of a folder of PDFs into one sheet, in file and page order.

What comes out:
  the sheet   every table one after another, its own header row on top, one blank row between tables
  the report  one line per table: file, pages, where it sits in the sheet, how it was found, and what was checked

What is checked and said out loud, never fixed quietly:
  a page with no text layer is named as a scan, a locked or broken file is named,
  a table found from spacing alone (no ruled lines) is marked so a person looks at it,
  a row that says Total is added up again from the rows above it and a difference is reported,
  text that sits on a page but landed in no table is counted, so a missed table shows up as a number.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from .core import SCAN_CHARS_PER_PAGE, _locked

# A table read from spacing has no ruled lines to prove it is a table, so it must look like one:
MIN_ROWS_SPACING = 3        # at least this many full lines in its body
CELL_GAP = 0.6              # a gap wider than this share of the letter height splits a line into two cells (a word space is about a quarter of the height)
WORD_GAP = 0.2              # a gap wider than this share of the letter size splits two words, for PDFs that carry no space characters
GUTTER = 6.0                # points of empty strip between two columns
COVER_SHARE = 0.1           # in a big table a strip is a column only where more than this share of the body lines have ink
PROSE_CELL = 40             # a two column find whose middle cell is longer than this many characters is two columns of prose
MAX_HEAD = 6                # at most this many heading lines are kept above the body
HEAD_CELL = 30              # a heading cell longer than this many characters is a sentence, the headings stop there
PILE = 3                    # this many numbers stacked in one ruled cell means the rules do not mark the rows
CORE_SHARE = 0.6            # lines with at least this share of the fullest line's cells are the body the columns are measured on
# All ten numbers are choices, not measurements. They were set on the test PDFs and five real government PDFs
# (Federal Reserve H.15, EIA table 1.1, IRS 1040 tax table, IRS Pub 15, IRS Pub 15-T), read 2026-11-12.
NUM = re.compile(r"^\(?-?[\$₹€£]?\s*-?(\d[\d,]*(\.\d+)?|\.\d+)\s*%?\)?$")   # .646 and -.678 count: a real energy table writes them so
TOTAL = re.compile(r"^\s*(?:\d{4}\s+)?(?:\d+\s*-?\s*month\s+)?(grand\s+|sub\s*-?\s*)?total\b", re.I)


@dataclass
class Found:
    file: str
    pages: list[int]
    rows: list[list[str]]
    method: str                       # ruled lines · spacing
    notes: list[str] = field(default_factory=list)
    first_row: int = 0                # 1 based row numbers in the sheet, filled in when the sheet is written
    last_row: int = 0

    @property
    def cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)


@dataclass
class Problem:
    file: str
    page: int | None
    what: str


def _clean(cell) -> str:
    return re.sub(r"[ \t]*\n[ \t]*", " ", str(cell)).strip() if cell is not None else ""


def number(cell: str):
    """The number in a cell, or None. (1,234.50) is negative, a currency sign and commas are dropped."""
    s = cell.strip()
    if not s or not NUM.match(s):
        return None
    neg = s.startswith("(") and s.endswith(")") or "-" in s
    digits = re.sub(r"[^\d.]", "", s)
    if not digits or digits == ".":
        return None
    v = float(digits)
    return -v if neg else v


def check_totals(rows: list[list[str]]) -> list[str]:
    """For each row labelled Total or Subtotal, add up the rows above it and compare, column by column.
    A total may cover its own block (the rows since the total before it) or every row above it. It is said to
    add up when either sum matches, and reported when neither does. A real energy table with a Total row after
    each year's twelve months gave 19 false alarms before the block sum was tried (2026-11-12)."""
    out = []
    label = lambda r: next((c for c in r if c.strip()), "")
    block_start = 1  # first data row of the current block (row 0 is the header)
    for i, row in enumerate(rows):
        m = TOTAL.match(label(row))
        if not m or i == 0:
            continue
        block = rows[block_start:i]
        grand = bool(m.group(1)) and m.group(1).strip().lower().startswith("grand")
        if not block and not grand:
            block_start = i + 1      # a total straight under another total has no rows of its own to add
            continue
        everything = [r for r in rows[1:i] if not TOTAL.match(label(r))]
        for col, cell in enumerate(row):
            stated = number(cell)
            if stated is None:
                continue
            sums, odd = [], []
            for body in (block, everything):
                cells = [r[col].strip() for r in body if col < len(r) and r[col].strip()]
                vals = [v for v in map(number, cells) if v is not None]
                odd.append([c for c in cells if number(c) is None])
                if vals:
                    sums.append((sum(vals), len(vals)))
            if not sums:
                continue
            if any(abs(s - stated) <= 0.005 * max(1, n) for s, n in sums):
                out.append(f"row {i + 1} column {col + 1}: total {cell.strip()} adds up")
            elif odd[0] or (not block and odd[1]):
                # a cell like R7.737 (a revised figure) is not a plain number, so the sum would leave it out and cry wolf
                bad = odd[0] or odd[1]
                out.append(f"row {i + 1} column {col + 1}: total {cell.strip()} not checked, {len(bad)} cell{'s' if len(bad) != 1 else ''} above not a plain number (for example {bad[0]})")
            else:
                out.append(f"row {i + 1} column {col + 1}: the table says {cell.strip()}, the rows above add to {sums[0][0]:,.2f}")
        block_start = i + 1
    return out


def _drop_empty(rows: list[list[str]]) -> list[list[str]]:
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return rows
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [c for c in range(width) if any(r[c] for r in rows)]
    return [[r[c] for c in keep] for r in rows]


def _lines_of_words(page) -> list[list[dict]]:
    words = sorted(page.extract_words(x_tolerance_ratio=WORD_GAP), key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    for w in words:
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in lines]


def _segments(line: list[dict]) -> list[tuple[float, float, str]]:
    """Words of one line joined into cells: a gap wider than CELL_GAP letters high starts a new cell."""
    segs = []
    for w in line:
        height = w["bottom"] - w["top"]
        if segs and w["x0"] - segs[-1][1] <= CELL_GAP * height:
            segs[-1] = (segs[-1][0], w["x1"], segs[-1][2] + " " + w["text"])
        else:
            segs.append((w["x0"], w["x1"], w["text"]))
    return segs


def _columns(core: list[list[tuple]]) -> list[tuple[float, float]]:
    """Column strips measured on the body lines. A strip counts as a column where at least a few lines have
    ink in it (one line for a small table, more than COVER_SHARE of the lines for a big one), so one heading
    that runs over two columns does not weld them together (a real Federal Reserve table did that 2026-11-12)."""
    need = 1 + int(COVER_SHARE * len(core))
    events = sorted([(x0, 1) for s in core for x0, _, _ in s] + [(x1, -1) for s in core for _, x1, _ in s], key=lambda e: (e[0], -e[1]))
    spans, depth, start = [], 0, None
    for x, d in events:
        depth += d
        if depth >= need and start is None:
            start = x
        elif depth < need and start is not None:
            spans.append([start, x])
            start = None
    cols = []
    for s in spans:
        if cols and s[0] - cols[-1][1] < GUTTER:
            cols[-1][1] = s[1]
        else:
            cols.append(s)
    return [tuple(c) for c in cols]


def _place(segs, cols) -> tuple[list[str], bool]:
    """(the line as a row of cells, whether every cell sat inside one column). A cell that runs over
    several columns, like a heading above a group of them, goes into the first column it touches."""
    row, clean = [""] * len(cols), True
    for x0, x1, text in segs:
        hit = [k for k, (c0, c1) in enumerate(cols) if x0 < c1 and x1 > c0]
        if len(hit) != 1:
            clean = False
        k = hit[0] if hit else min(range(len(cols)), key=lambda k: abs(cols[k][0] - x0))
        row[k] = (row[k] + " " + text).strip()
    return row, clean


def _spacing_tables(page) -> list[tuple[tuple, list[list[str]], str]]:
    """Tables with no ruled lines. pdfplumber's own text strategy cut prose into columns and swallowed the title
    on the first test run (2026-11-12), so this looks for what a person sees: lines in a row that each hold two or
    more cells, with empty strips (gutters) running down between the cells of all of them.
    The columns are measured on the fullest lines (the body). A line above the body that does not sit in those
    columns is kept as a heading when it has three or more cells and cut as a title when it has fewer. The first
    line below the body that does not sit in the columns ends the table. Found on a real EIA table 2026-11-12:
    its title line ran across the gutters and the first version threw the whole table away."""
    lines = [(_segments(l), l) for l in _lines_of_words(page)]
    out, i = [], 0
    while i < len(lines):
        if len(lines[i][0]) < 2:
            i += 1
            continue
        j = i
        while j + 1 < len(lines) and any(j + n < len(lines) and len(lines[j + n][0]) >= 2 for n in (1, 2, 3)):
            j += 1                                      # up to two lines of one cell (a group label) may sit inside a table
        run = lines[i:j + 1]
        most = max(len(s) for s, _ in run)
        full = [k for k, (s, _) in enumerate(run) if len(s) >= max(2, CORE_SHARE * most)]
        # the body is the longest stretch of full lines with at most two thinner lines between neighbours. A footnote
        # far below a real energy table happened to have ten cells and welded three columns into one (2026-11-12).
        stretches = [[full[0]]]
        for k in full[1:]:
            if k - stretches[-1][-1] <= 3:
                stretches[-1].append(k)
            else:
                stretches.append([k])
        core_idx = max(stretches, key=len)
        cols = _columns([run[k][0] for k in core_idx]) if len(core_idx) >= MIN_ROWS_SPACING else []   # a body of fewer full lines is too thin to trust
        kept, spans, end = [], 0, len(run)
        if len(cols) >= 2:
            first = core_idx[0]
            head = []
            for k in range(first - 1, -1, -1):          # upward from the body: headings stay, a title stops it
                row, clean = _place(run[k][0], cols)
                if (not clean and len(run[k][0]) < 3) or len(head) >= MAX_HEAD or any(len(c) > HEAD_CELL for c in row):
                    break                                # a title, or sentences from a text column beside the table
                spans += 0 if clean else 1
                head.append((k, row))
            kept = list(reversed(head))
            for k in range(first, len(run)):
                row, clean = _place(run[k][0], cols)
                if not clean and k > core_idx[-1]:      # below the body: a sentence or a note, the table is over
                    end = k
                    break
                spans += 0 if clean else 1
                kept.append((k, row))
        rows = _drop_empty([r for _, r in kept])
        if rows and len(rows[0]) == 2:
            lengths = sorted(len(c) for r in rows for c in r if c)
            if lengths[len(lengths) // 2] > PROSE_CELL:   # two columns of sentences are a page layout, not a table
                rows = []
        if rows:
            words = [w for k, _ in kept for w in run[k][1]]
            bbox = (min(w["x0"] for w in words), min(w["top"] for w in words), max(w["x1"] for w in words), max(w["bottom"] for w in words))
            out.append((bbox, rows, "spacing" + (f", {spans} line{'s' if spans != 1 else ''} had a cell running over several columns" if spans else "")))
        i = i + max(end, 1) if end < len(run) else j + 1
    return out


def _unstack(raw: list[list]) -> tuple[list[list], bool]:
    """A table ruled between rows only in places prints several rows inside one ruled cell, one value per line.
    Where every filled cell of a row has the same number of lines and one of them is all numbers, the row is
    split back into that many rows. Returns the rows and whether a pile of numbers is still left in some cell."""
    out, piled = [], False
    for r in raw:
        parts = [str(c).split("\n") if c not in (None, "") else [] for c in r]
        counts = {len(p) for p in parts if p}
        numeric = any(len(p) > 1 and all(number(x) is not None for x in p) for p in parts)
        if len(counts) == 1 and counts != {1} and numeric:
            n = counts.pop()
            out.extend([[p[k] if p else "" for p in parts] for k in range(n)])
        else:
            out.append(r)
            piled = piled or any(sum(1 for x in p if number(x) is not None) >= PILE for p in parts)
    return out, piled


def _page_tables(page) -> tuple[list[tuple[tuple, list[list[str]], str]], int]:
    """([(bbox, rows, method)] top to bottom, characters of text on the page that are in no table)."""
    found, piled = [], []
    for t in page.find_tables():
        raw, was_piled = _unstack(t.extract())
        rows = _drop_empty([[_clean(c) for c in r] for r in raw])
        if rows and max(len(r) for r in rows) >= 2:
            found.append((t.bbox, rows, "ruled lines"))
            if was_piled:
                piled.append(found[-1])
    cells = lambda rows: sum(1 for r in rows for c in r if c)
    inside = lambda a, b: a[0] >= b[0] - 2 and a[1] >= b[1] - 2 and a[2] <= b[2] + 2 and a[3] <= b[3] + 2
    apart = lambda a, b: a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
    for sp in _spacing_tables(page):
        # a page with a few drawn rules around a table that is mostly held by spacing (a real Federal Reserve release,
        # 2026-11-12): the ruled read caught a strip of it. The spacing read replaces ruled finds that lie inside it
        # when it holds more than twice their cells. A spacing find that touches no ruled table is simply added.
        # One that only overlaps a ruled table is dropped, the ruled lines are the better evidence.
        within = [f for f in found if inside(f[0], sp[0])]
        touching = [f for f in found if not apart(f[0], sp[0])]
        if touching and all(f in piled for f in touching):
            # rules between the columns only, so each ruled cell held a whole column of values in a pile (the IRS
            # tax table, 2026-11-12). The lines of text are the rows there, and the spacing read follows them.
            found = [f for f in found if f not in touching] + [sp]
        elif within and cells(sp[1]) > 2 * sum(cells(f[1]) for f in within) and all(f in within or apart(f[0], sp[0]) for f in found):
            found = [f for f in found if f not in within] + [sp]
        elif all(apart(f[0], sp[0]) for f in found):
            found.append(sp)
    found.sort(key=lambda f: f[0][1])
    boxes = [f[0] for f in found]

    def outside(obj):
        if obj.get("object_type") != "char":
            return False
        cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
        return not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)

    loose = len(re.sub(r"\s", "", page.filter(outside).extract_text() or "")) if boxes else len(re.sub(r"\s", "", page.extract_text() or ""))
    return found, loose


def extract_tables(path: Path, join_pages: bool = True) -> tuple[list[Found], list[Problem], dict]:
    """Every table in one PDF, the problems met, and counts (pages, loose characters by page)."""
    name = Path(path).name
    if _locked(Path(path)):
        return [], [Problem(name, None, "password protected, not opened")], {"pages": 0, "loose": {}}
    try:
        pdf = pdfplumber.open(str(path))
    except Exception as e:
        return [], [Problem(name, None, f"could not be opened ({type(e).__name__})")], {"pages": 0, "loose": {}}
    tables, problems, loose_by_page = [], [], {}
    last_on_prev: Found | None = None
    with pdf:
        for i, page in enumerate(pdf.pages, start=1):
            try:
                chars = len(re.sub(r"\s", "", page.extract_text() or ""))
                found, loose = _page_tables(page)
            except Exception as e:
                problems.append(Problem(name, i, f"page could not be read ({type(e).__name__})"))
                last_on_prev = None
                continue
            if chars < SCAN_CHARS_PER_PAGE:
                problems.append(Problem(name, i, f"no text layer ({chars} characters), looks scanned, nothing read from it"))
                last_on_prev = None
                continue
            loose_by_page[i] = loose
            if not found:
                last_on_prev = None
                continue
            for k, (bbox, rows, method) in enumerate(found):
                prev = last_on_prev if (k == 0 and join_pages) else None
                if prev is not None and prev.cols == max(len(r) for r in rows) and prev.method == method.split(",")[0] and rows[0] == prev.rows[0]:
                    # the same header again on the next page: one table printed over two pages
                    prev.rows.extend(rows[1:])
                    prev.pages.append(i)
                    if "runs over pages, the repeated header was dropped" not in prev.notes:
                        prev.notes.append("runs over pages, the repeated header was dropped")
                    cur = prev
                else:
                    cur = Found(name, [i], rows, method)
                    if method.startswith("spacing"):
                        cur.notes.append("no ruled lines, columns read from spacing, look at this one")
                        if "," in method:
                            cur.notes.append(method.split(", ", 1)[1])
                        cur.method = "spacing"
                    tables.append(cur)
            last_on_prev = cur
    for t in tables:
        blank = sum(1 for r in t.rows[1:] for c in r if not c)
        if blank:
            t.notes.append(f"{blank} empty cell" + ("s" if blank != 1 else ""))
        t.notes.extend(check_totals(t.rows))
    return tables, problems, {"pages": len(loose_by_page) + sum(1 for p in problems if p.page), "loose": loose_by_page}


def _natural(p: Path):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", p.name)]


def run_tables(folder: Path, out_csv: Path, report_csv: Path | None = None, join_pages: bool = True, source_columns: bool = False) -> dict:
    pdfs = sorted((p for p in Path(folder).iterdir() if p.suffix.lower() == ".pdf"), key=_natural)
    if not pdfs:
        raise ValueError(f"no .pdf files in {folder}")
    report_csv = report_csv or out_csv.with_name(out_csv.stem + "_report.csv")
    all_tables, all_problems, loose_rows = [], [], []
    for p in pdfs:
        tables, problems, info = extract_tables(p, join_pages)
        all_tables += tables
        all_problems += problems
        if not tables and not problems:
            all_problems.append(Problem(p.name, None, "no table found in this file"))
        for page, n in info["loose"].items():
            if n:
                loose_rows.append((p.name, page, n))
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    line = 0
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for n, t in enumerate(all_tables):
            if n:
                w.writerow([])
                line += 1
            t.first_row = line + 1
            for r in t.rows:
                w.writerow(([t.file, "-".join(map(str, sorted(set(t.pages))))] if source_columns else []) + r)
                line += 1
            t.last_row = line
    with open(report_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["kind", "file", "pages", "sheet_rows", "rows", "columns", "found_by", "notes"])
        for t in all_tables:
            pages = sorted(set(t.pages))
            w.writerow(["table", t.file, f"{pages[0]}-{pages[-1]}" if len(pages) > 1 else pages[0], f"{t.first_row}-{t.last_row}",
                        len(t.rows), t.cols, t.method, "; ".join(t.notes)])
        for pr in all_problems:
            w.writerow(["problem", pr.file, pr.page or "", "", "", "", "", pr.what])
        for f, page, n in loose_rows:
            w.writerow(["text outside tables", f, page, "", "", "", "", f"{n} characters on this page are in no table (titles and notes are normal, a large number can mean a missed table)"])
    mismatches = sum(1 for t in all_tables for n in t.notes if "the rows above add to" in n)
    return {"files": len(pdfs), "tables": len(all_tables), "rows": sum(len(t.rows) for t in all_tables), "problems": len(all_problems),
            "total_mismatches": mismatches, "by_spacing": sum(1 for t in all_tables if t.method == "spacing"),
            "results": all_tables, "problem_list": all_problems, "report": report_csv}
