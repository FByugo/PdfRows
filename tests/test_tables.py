"""Table mode, checked cell by cell on PDFs built here so every answer is known."""
import csv

import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from pdfrows.tables import check_totals, extract_tables, number, run_tables

GRID = TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)])
INVOICE = [["Item", "Qty", "Unit price", "Amount"],
           ["Ceiling fan, 48 inch", "3", "$120.00", "$360.00"],
           ["Wall bracket", "10", "$4.50", "$45.00"],
           ["Remote, model R-2", "2", "$15.25", "$30.50"],
           ["Total", "", "", "$435.50"]]
STAFF = [["Name", "Team", "Start"], ["Ana Lopez", "Sales", "2021-03-01"], ["Ben Ode", "", "2022-11-15"], ["Cy Park", "Ops", "2019-07-09"]]


def ruled_pdf(path, tables, text_before="Quarterly report"):
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    s = getSampleStyleSheet()["Normal"]
    flow = [Paragraph(text_before, s), Spacer(1, 12)]
    for t in tables:
        if t == "break":
            flow.append(PageBreak())
            continue
        flow += [Table(t, style=GRID, repeatRows=1), Spacer(1, 24)]
    doc.build(flow)


def spaced_pdf(path, rows, xs=(60, 250, 340, 440)):
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(60, 740, "Price list, spring")
    y = 700
    for r in rows:
        for x, cell in zip(xs, r):
            c.drawString(x, y, cell)
        y -= 18
    c.save()


def prose_pdf(path):
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    s = getSampleStyleSheet()["Normal"]
    text = ("This letter confirms the order placed on the third of March. The fans will ship in two lots, "
            "the first of forty units and the second of sixty, and each lot is paid on delivery. ") * 6
    doc.build([Paragraph(text, s), Spacer(1, 12), Paragraph(text, s)])


def scan_pdf(path):
    c = canvas.Canvas(str(path), pagesize=letter)
    c.rect(50, 50, 500, 650, fill=1)
    c.save()


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "pdfs"
    d.mkdir()
    ruled_pdf(d / "01_invoice.pdf", [INVOICE])
    ruled_pdf(d / "02_two_tables.pdf", [STAFF, INVOICE])
    long = [["Order", "Customer", "Amount"]] + [[f"A{n:03d}", f"Customer {n}", f"{n * 10}.00"] for n in range(1, 91)]
    ruled_pdf(d / "03_long.pdf", [long])
    wrong = [r[:] for r in INVOICE]
    wrong[-1][-1] = "$453.50"
    ruled_pdf(d / "04_wrong_total.pdf", [wrong])
    spaced_pdf(d / "05_spaced.pdf", [r for r in INVOICE])
    prose_pdf(d / "06_prose.pdf")
    scan_pdf(d / "07_scan.pdf")
    (d / "08_broken.pdf").write_bytes(b"%PDF-1.4 not a pdf")
    ruled_pdf(d / "9_nine.pdf", [INVOICE])
    ruled_pdf(d / "10_last.pdf", [STAFF])
    return d


def test_ruled_table_comes_out_cell_for_cell(folder):
    tables, problems, _ = extract_tables(folder / "01_invoice.pdf")
    assert [t.rows for t in tables] == [INVOICE] and tables[0].method == "ruled lines" and not problems


def test_two_tables_on_a_page_stay_two_and_in_order(folder):
    tables, _, _ = extract_tables(folder / "02_two_tables.pdf")
    assert [t.rows for t in tables] == [STAFF, INVOICE]
    assert "1 empty cell" in tables[0].notes


def test_table_over_three_pages_is_one_table_with_one_header(folder):
    tables, _, _ = extract_tables(folder / "03_long.pdf")
    assert len(tables) == 1
    t = tables[0]
    assert len(t.rows) == 91 and len(set(t.pages)) >= 2
    assert sum(1 for r in t.rows if r == ["Order", "Customer", "Amount"]) == 1
    assert [r[0] for r in t.rows[1:]] == [f"A{n:03d}" for n in range(1, 91)]
    assert any("repeated header was dropped" in n for n in t.notes)


def test_page_joining_can_be_turned_off(folder):
    tables, _, _ = extract_tables(folder / "03_long.pdf", join_pages=False)
    assert len(tables) >= 2 and sum(len(t.rows) - 1 for t in tables) == 90


def test_a_right_total_is_confirmed_and_a_wrong_one_is_reported(folder):
    good, _, _ = extract_tables(folder / "01_invoice.pdf")
    bad, _, _ = extract_tables(folder / "04_wrong_total.pdf")
    assert any("total $435.50 adds up" in n for n in good[0].notes)
    assert any("the table says $453.50, the rows above add to 435.50" in n for n in bad[0].notes)
    assert bad[0].rows[-1][-1] == "$453.50"  # reported, never corrected


def test_table_without_lines_is_read_and_marked(folder):
    tables, _, _ = extract_tables(folder / "05_spaced.pdf")
    assert len(tables) == 1 and tables[0].method == "spacing"
    assert tables[0].rows == INVOICE
    assert any("look at this one" in n for n in tables[0].notes)


def test_prose_is_not_turned_into_a_table(folder):
    tables, problems, _ = extract_tables(folder / "06_prose.pdf")
    assert tables == [] and problems == []


def test_scan_and_broken_file_are_named(folder):
    _, scan, _ = extract_tables(folder / "07_scan.pdf")
    _, broken, _ = extract_tables(folder / "08_broken.pdf")
    assert "looks scanned" in scan[0].what and scan[0].page == 1
    assert "could not be opened" in broken[0].what


def test_one_sheet_in_file_order_with_a_blank_row_between(folder, tmp_path):
    out = tmp_path / "out" / "tables.csv"
    s = run_tables(folder, out)
    rows = list(csv.reader(open(out, encoding="utf-8")))
    assert s["files"] == 10 and s["tables"] == 8 and s["total_mismatches"] == 1 and s["by_spacing"] == 1
    assert rows[:5] == INVOICE and rows[5] == [] and rows[6:10] == STAFF and rows[10] == []
    assert rows[-4:] == STAFF and rows[-10:-5] == INVOICE  # 9_nine.pdf then 10_last.pdf: files go by number, as text 10 would come before 9
    assert [r["file"] for r in csv.DictReader(open(s["report"], encoding="utf-8")) if r["kind"] == "table"][-2:] == ["9_nine.pdf", "10_last.pdf"]
    rep = list(csv.DictReader(open(s["report"], encoding="utf-8")))
    first = rep[0]
    assert (first["kind"], first["file"], first["sheet_rows"], first["rows"], first["columns"]) == ("table", "01_invoice.pdf", "1-5", "5", "4")
    for r in rep:
        if r["kind"] == "table":
            a, b = map(int, r["sheet_rows"].split("-"))
            assert b - a + 1 == int(r["rows"]) and rows[a - 1] != [] and (b == len(rows) or rows[b] == [])
    whats = {(r["file"], r["notes"].split(",")[0].split(" (")[0]) for r in rep if r["kind"] == "problem"}
    assert ("06_prose.pdf", "no table found in this file") in whats
    assert any(f == "07_scan.pdf" for f, _ in whats) and any(f == "08_broken.pdf" for f, _ in whats)
    assert any(r["kind"] == "text outside tables" and r["file"] == "01_invoice.pdf" for r in rep)


def test_source_columns(folder, tmp_path):
    out = tmp_path / "t.csv"
    run_tables(folder, out, source_columns=True)
    rows = list(csv.reader(open(out, encoding="utf-8")))
    assert rows[0] == ["01_invoice.pdf", "1"] + INVOICE[0]


def test_numbers():
    assert number("$1,234.50") == 1234.5 and number("(1,234.50)") == -1234.5 and number("-12") == -12
    assert number("₹1,23,456.00") == 123456.0 and number("12%") == 12
    assert number(".646") == 0.646 and number("-.678") == -0.678 and number(".") is None and number("R7.737") is None
    assert number("A12") is None and number("") is None and number("2021-03-01") is None


def test_subtotals_then_grand_total():
    rows = [["Item", "Amount"], ["a", "10"], ["b", "20"], ["Subtotal", "30"], ["c", "5"], ["Subtotal", "6"], ["Grand total", "35"]]
    notes = check_totals(rows)
    assert "row 4 column 2: total 30 adds up" in notes
    assert any(n.startswith("row 6 column 2: the table says 6, the rows above add to 5.00") for n in notes)
    assert "row 7 column 2: total 35 adds up" in notes


def test_yearly_blocks_each_with_their_own_total_and_a_marked_figure():
    rows = [["Month", "Amount"], ["Jan", "1.5"], ["Feb", ".5"], ["Total", "2.0"], ["Jan", "R3.0"], ["Feb", "1.0"], ["Total", "4.0"],
            ["Jan", "2"], ["Feb", "2"], ["Total", "5"], ["2025 Total", "9"]]
    notes = check_totals(rows)
    assert "row 4 column 2: total 2.0 adds up" in notes                     # its own block, not every row above
    assert any(n.startswith("row 7 column 2: total 4.0 not checked, 1 cell above not a plain number (for example R3.0)") for n in notes)
    assert any(n.startswith("row 10 column 2: the table says 5, the rows above add to 4.00") for n in notes)
    assert not any(n.startswith("row 11") for n in notes)                    # a total under a total has nothing to add


def test_rows_stacked_inside_one_ruled_cell_are_split_back(tmp_path):
    from reportlab.platypus import SimpleDocTemplate as D, Table as T
    stacked = [["Quarter", "Limit"], ["Q1 2024\nQ2 2024\nQ3 2024", "$12,000\n12,500\n13,000"], ["Wage base (each\nemployee)", "note"]]
    D(str(tmp_path / "s.pdf"), pagesize=letter).build([T(stacked, style=GRID)])
    tables, _, _ = extract_tables(tmp_path / "s.pdf")
    assert tables[0].rows == [["Quarter", "Limit"], ["Q1 2024", "$12,000"], ["Q2 2024", "12,500"], ["Q3 2024", "13,000"], ["Wage base (each employee)", "note"]]


def test_command_line(folder, tmp_path):
    from typer.testing import CliRunner
    from pdfrows.tables_cli import app
    out = tmp_path / "t.csv"
    r = CliRunner().invoke(app, [str(folder), "--out", str(out)])
    assert r.exit_code == 0 and "10 files, 8 tables" in r.output and "1 total did not add up" in r.output
    assert "problem: 07_scan.pdf page 1" in r.output and "1 table had no ruled lines" in r.output
    assert CliRunner().invoke(app, [str(folder), "--out", str(out), "--strict"]).exit_code == 1
    clean = tmp_path / "clean"
    clean.mkdir()
    ruled_pdf(clean / "a.pdf", [INVOICE])
    assert CliRunner().invoke(app, [str(clean), "--out", str(out), "--strict"]).exit_code == 0
    empty = tmp_path / "empty"
    empty.mkdir()
    assert CliRunner().invoke(app, [str(empty), "--out", str(out)]).exit_code == 2


def test_two_columns_of_sentences_are_not_a_table(tmp_path):
    c = canvas.Canvas(str(tmp_path / "cols.pdf"), pagesize=letter)
    left = ["a Coal, natural gas, crude oil, and natural gas plant liquids are counted.", "b See the tables in section ten for notes on how each series is built.",
            "c Net imports are imports with the exports taken away, in the same units.", "d This line covers stock change and the balancing item for each fuel."]
    right = ["Notes: totals may not equal the sum of their parts because of rounding.", "Coverage is the fifty states and the District of Columbia for all years.",
             "Web page: the monthly release carries every year back to nineteen forty nine.", "Sources: production and trade come from the tables named in each column."]
    c.setFont("Helvetica", 6)
    for k, (a, b) in enumerate(zip(left, right)):
        c.drawString(40, 700 - 9 * k, a)
        c.drawString(320, 700 - 9 * k, b)
    c.save()
    tables, _, _ = extract_tables(tmp_path / "cols.pdf")
    assert tables == []


def test_title_above_and_note_below_stay_out_of_an_unruled_table(tmp_path):
    c = canvas.Canvas(str(tmp_path / "t.pdf"), pagesize=letter)
    c.drawString(60, 740, "Table 4")
    c.drawString(140, 740, "Quarterly sales by region, in thousands of dollars, all channels")
    y = 716
    for r in [["Region", "Q1", "Q2", "Q3"], ["North", "10", "12", "14"], ["South", "8", "9", "11"], ["West", "20", "21", "19"], ["Total", "38", "42", "44"]]:
        for x, cell in zip((60, 200, 300, 400), r):
            c.drawString(x, y, cell)
        y -= 18
    c.drawString(60, y, "Source: the annual survey of regional offices")     # a note in two parts, each lying across the columns
    c.drawString(330, y, "Revised figures are marked R")
    c.drawString(60, y - 18, "Figures are rounded and may be revised in the next release of this report.")
    c.save()
    tables, _, _ = extract_tables(tmp_path / "t.pdf")
    assert len(tables) == 1 and tables[0].rows[0] == ["Region", "Q1", "Q2", "Q3"] and tables[0].rows[-1] == ["Total", "38", "42", "44"]
    assert sum("adds up" in n for n in tables[0].notes) == 3


def test_two_lines_of_paired_facts_are_not_a_table(tmp_path):
    c = canvas.Canvas(str(tmp_path / "head.pdf"), pagesize=letter)
    for k, (a, b) in enumerate([("Invoice 1042", "Date 2024-03-01"), ("Customer Ana Lopez", "Terms 30 days")]):
        c.drawString(60, 740 - 18 * k, a)
        c.drawString(360, 740 - 18 * k, b)
    c.drawString(60, 680, "Thank you for the order. Payment is due within thirty days of the date above.")
    c.save()
    tables, _, _ = extract_tables(tmp_path / "head.pdf")
    assert tables == []


def test_a_heading_over_two_columns_does_not_weld_them(tmp_path):
    c = canvas.Canvas(str(tmp_path / "span.pdf"), pagesize=letter)
    xs = (60, 200, 280, 360, 440)
    c.drawString(60, 740, "Instrument")
    c.drawString(200, 740, "Monday")
    c.drawString(290, 740, "Week ending average")      # lies across the third and fourth columns
    c.drawString(440, 740, "Month")
    body = [[f"{n}-month", f"0.{n:02d}", f"1.{n:02d}", f"2.{n:02d}", f"3.{n:02d}"] for n in range(1, 13)]
    for k, r in enumerate(body):
        for x, cell in zip(xs, r):
            c.drawString(x, 722 - 14 * k, cell)
    c.save()
    tables, _, _ = extract_tables(tmp_path / "span.pdf")
    assert len(tables) == 1 and tables[0].cols == 5 and tables[0].rows[1:] == body
    assert tables[0].rows[0] == ["Instrument", "Monday", "Week ending average", "", "Month"]
    assert any("1 line had a cell running over several columns" in n for n in tables[0].notes)
