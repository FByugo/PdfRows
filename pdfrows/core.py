"""Turn a folder of PDFs (survey replies, forms, invoices) into one CSV row per file.

For each field it tries, in order:
  1. a fillable form field with that name (exact value, no guessing),
  2. a label in the text, like "Email:" or "Q3. How did you hear about us?", taking the answer after it.
Every value says where it came from (form or page number), every missing field is listed by name,
and a PDF with no text layer (a scan) is reported as needing OCR instead of producing an empty row.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

SCAN_CHARS_PER_PAGE = 20  # fewer real characters than this per page means there is no usable text layer


@dataclass
class Field:
    name: str
    labels: list[str]                  # text labels to look for, tried in order
    form_names: list[str] = field(default_factory=list)  # fillable field names to try first
    pattern: str | None = None         # optional check on the value, e.g. an email regex
    multiline: bool = False            # answer may run over several lines until the next label
    ignore_case: bool = False          # labels are matched as written: "Name" will not match wrapped prose "name on line 2"


@dataclass
class Spec:
    fields: list[Field]

    @classmethod
    def load(cls, path: Path) -> "Spec":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        fs = []
        for f in data["fields"]:
            unknown = set(f) - set(Field.__dataclass_fields__)
            if unknown:
                raise ValueError(f"field {f.get('name')!r} has unknown keys {sorted(unknown)}")
            if not f.get("labels") and not f.get("form_names"):
                raise ValueError(f"field {f['name']!r} needs labels or form_names")
            f.setdefault("labels", [])
            if f.get("pattern"):
                re.compile(f["pattern"])
            fs.append(Field(**f))
        names = [f.name for f in fs]
        if len(set(names)) != len(names):
            raise ValueError("two fields share a name")
        return cls(fs)


@dataclass
class FileResult:
    file: str
    status: str            # ok · partial · needs_ocr · encrypted · unreadable
    values: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    invalid: list = field(default_factory=list)
    note: str = ""


def _norm(s: str) -> str:
    return re.sub(r"[ \t]+", " ", s).strip()


def _form_values(path: Path) -> tuple[dict, bool]:
    """(filled values by field name, whether the PDF has fillable fields at all)."""
    try:
        r = PdfReader(str(path))
        if r.is_encrypted:
            r.decrypt("")
        fields = r.get_fields() or {}
    except (FileNotDecryptedError, PdfReadError, ValueError):
        return {}, False
    out = {}
    for name, f in fields.items():
        v = f.get("/V")
        if v is None:
            continue
        v = str(v)
        if v.startswith("/"):  # checkbox / radio export value, like /Yes
            v = v[1:]
        if v.strip() and v != "Off":
            out[name] = v.strip()
    return out, bool(fields)


def _find_label(lines: list[tuple[int, str]], all_labels: list[str], f: Field):
    """Answer after the first label of f that appears: same line after the label, else the next line(s)."""
    other = [re.escape(l) for l in all_labels if l not in f.labels]
    stop = re.compile(r"^\s*(?:\S+\s+)?(?:" + "|".join(other) + r")") if other else None
    for label in f.labels:
        # the label must START the line (after an optional "1." / "Q3." / "a)" marker); matching it anywhere
        # picked "name" out of W-9 instruction prose on its first real run (2026-10-14)
        rx = re.compile(r"^\s*(?:[A-Za-z]?\d{1,3}[.)]?\s+|[A-Za-z]?\d{0,3}[.)]\s*)?" + re.escape(label) + r"\s*[:\-]?\s*(.*)$",
                        re.I if f.ignore_case else 0)
        for idx, (page, line) in enumerate(lines):
            m = rx.search(line)
            if not m:
                continue
            parts = [m.group(1).strip()] if m.group(1).strip() else []
            j = idx + 1
            while j < len(lines) and (f.multiline or not parts):
                nxt = lines[j][1].strip()
                if not nxt or (stop and stop.match(nxt)):
                    break
                parts.append(nxt)
                j += 1
                if not f.multiline:
                    break
            if parts:
                return " ".join(parts), page
    return None, None


def _locked(path: Path) -> bool:
    """True when the file is encrypted with a real password. An empty owner password (common on
    'protected' PDFs that open fine) does not count as locked."""
    try:
        r = PdfReader(str(path))
        return bool(r.is_encrypted) and r.decrypt("") == 0
    except (PdfReadError, ValueError, OSError):
        return False  # not readable as a PDF at all; pdfplumber below reports it as unreadable


def extract_file(path: Path, spec: Spec) -> FileResult:
    res = FileResult(file=path.name, status="ok")
    if _locked(path):
        # asked directly, not inferred from an error string: pdfminer raised an EMPTY message for this (2026-10-14)
        res.status, res.note, res.missing = "encrypted", "password protected, not opened", [f.name for f in spec.fields]
        return res
    try:
        with pdfplumber.open(str(path)) as pdf:
            pages = [(i + 1, p.extract_text() or "") for i, p in enumerate(pdf.pages)]
    except Exception as e:  # pdfplumber raises several unrelated types for broken and locked files
        res.status, res.note = "unreadable", f"{type(e).__name__}: {str(e)[:150] or 'no detail given'}"
        res.missing = [f.name for f in spec.fields]
        return res

    form, fillable = _form_values(path)
    text_chars = sum(len(re.sub(r"\s", "", t)) for _, t in pages)
    lines = [(pg, _norm(l)) for pg, t in pages for l in t.splitlines() if _norm(l)]
    all_labels = [l for f in spec.fields for l in f.labels]

    for f in spec.fields:
        val, src = None, None
        for fn in f.form_names:
            if fn in form:
                val, src = form[fn], "form"
                break
        # On a fillable PDF the answers live in the fields; the printed text after a label is the form's own
        # instructions. The blank IRS W-9 returned ". An entry is required..." as a name before this rule.
        if val is None and f.labels and not fillable:
            val, page = _find_label(lines, all_labels, f)
            src = f"page {page}" if val is not None else None
        if val is None:
            res.missing.append(f.name)
            continue
        if f.pattern and not re.fullmatch(f.pattern, val):
            res.invalid.append(f"{f.name}={val!r}")
        res.values[f.name], res.sources[f.name] = val, src

    if fillable and res.missing:
        res.note = "fillable form: " + ", ".join(res.missing) + " not filled in (printed text is not read as an answer)"
    if not fillable and not res.values and text_chars < SCAN_CHARS_PER_PAGE * max(1, len(pages)):
        # only when nothing came out AND there is almost no text: a short real document is not a scan
        res.status, res.note = "needs_ocr", f"no text layer ({text_chars} characters on {len(pages)} pages); looks scanned"
    elif res.missing or res.invalid:
        res.status = "partial"
    return res


def run(folder: Path, spec: Spec, out_csv: Path) -> dict:
    pdfs = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() == ".pdf")
    if not pdfs:
        raise ValueError(f"no .pdf files in {folder}")
    results = [extract_file(p, spec) for p in pdfs]
    names = [f.name for f in spec.fields]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "status", *names, *[f"{n}_source" for n in names], "missing", "invalid", "note"])
        for r in results:
            w.writerow([r.file, r.status, *[r.values.get(n, "") for n in names],
                        *[r.sources.get(n, "") for n in names], "; ".join(r.missing), "; ".join(r.invalid), r.note])
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {"files": len(results), "counts": counts, "results": results}
