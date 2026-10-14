import csv
import json

import pytest
from typer.testing import CliRunner

from pdfrows.cli import app
from pdfrows.core import Spec, run


def rows(tmp_path, folder, spec_file):
    s = run(folder, Spec.load(spec_file), tmp_path / "out.csv")
    return s, {r["file"]: r for r in csv.DictReader(open(tmp_path / "out.csv"))}


def test_every_file_gets_a_row_with_a_status(tmp_path, folder, spec_file):
    s, r = rows(tmp_path, folder, spec_file)
    assert s["files"] == 10 and "notes.txt" not in r
    assert {k: v["status"] for k, v in r.items()} == {
        "a_typed.pdf": "ok", "b_twopage.pdf": "partial", "c_bademail.pdf": "partial", "d_form.pdf": "partial",
        "e_scan.pdf": "needs_ocr", "f_locked.pdf": "encrypted", "g_broken.pdf": "unreadable",
        "h_prose.pdf": "partial", "i_short.pdf": "partial", "j_blankform.pdf": "partial"}


def test_typed_survey_values(tmp_path, folder, spec_file):
    a = rows(tmp_path, folder, spec_file)[1]["a_typed.pdf"]
    assert a["name"] == "Ana Lopez" and a["email"] == "ana@example.com"
    assert a["heard"] == "A friend told me"                                   # answer on the next line
    assert a["comments"] == "Loved the onboarding. Wish exports were faster."  # multiline, stops at Q3
    assert a["rating"] == "4" and a["name_source"] == "page 1"


def test_two_pages_and_same_line_answer(tmp_path, folder, spec_file):
    b = rows(tmp_path, folder, spec_file)[1]["b_twopage.pdf"]
    assert b["heard"] == "Search" and b["email_source"] == "page 2"
    assert b["missing"] == "comments"


def test_validation_flags_not_drops(tmp_path, folder, spec_file):
    c = rows(tmp_path, folder, spec_file)[1]["c_bademail.pdf"]
    assert c["email"] == "cy at example dot com"  # kept as written so a person can see it
    assert "email='cy at example dot com'" in c["invalid"] and "rating='9'" in c["invalid"]


def test_label_inside_prose_is_not_an_answer(tmp_path, folder, spec_file):
    assert rows(tmp_path, folder, spec_file)[1]["h_prose.pdf"]["name"] == "Hal Kim"


def test_short_real_text_is_not_a_scan(tmp_path, folder, spec_file):
    i = rows(tmp_path, folder, spec_file)[1]["i_short.pdf"]
    assert i["rating"] == "3" and "scanned" not in i["note"]


def test_blank_fillable_form_reads_no_printed_text(tmp_path, folder, spec_file):
    j = rows(tmp_path, folder, spec_file)[1]["j_blankform.pdf"]
    assert j["name"] == "" and j["email"] == "" and "not filled in" in j["note"]


def test_fillable_form_wins_over_text(tmp_path, folder, spec_file):
    d = rows(tmp_path, folder, spec_file)[1]["d_form.pdf"]
    assert d["name"] == "Di Hart" and d["name_source"] == "form" and d["email_source"] == "form"


def test_scan_locked_and_broken_are_named_not_blank(tmp_path, folder, spec_file):
    r = rows(tmp_path, folder, spec_file)[1]
    assert "looks scanned" in r["e_scan.pdf"]["note"]
    assert r["f_locked.pdf"]["note"] == "password protected, not opened" and r["f_locked.pdf"]["name"] == ""
    assert r["g_broken.pdf"]["note"]


@pytest.mark.parametrize("bad,msg", [
    ({"fields": [{"name": "x"}]}, "needs labels or form_names"),
    ({"fields": [{"name": "x", "labels": ["X"], "colour": 1}]}, "unknown keys"),
    ({"fields": [{"name": "x", "labels": ["X"]}, {"name": "x", "labels": ["Y"]}]}, "share a name"),
])
def test_bad_specs(tmp_path, bad, msg):
    p = tmp_path / "s.json"; p.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match=msg):
        Spec.load(p)


def test_cli(tmp_path, folder, spec_file):
    r = CliRunner()
    res = r.invoke(app, [str(folder), "--spec", str(spec_file), "--out", str(tmp_path / "o.csv")])
    assert res.exit_code == 0 and res.output.startswith("10 files:")
    assert r.invoke(app, [str(folder), "--spec", str(spec_file), "--out", str(tmp_path / "o.csv"), "--strict"]).exit_code == 1
    empty = tmp_path / "empty"; empty.mkdir()
    assert r.invoke(app, [str(empty), "--spec", str(spec_file)]).exit_code == 2
