"""Build the test PDFs from scratch so every case is known exactly."""
import json

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas


def text_pdf(path, pages, encrypt=None):
    c = canvas.Canvas(str(path), pagesize=letter, encrypt=encrypt)
    for lines in pages:
        y = 740
        for line in lines:
            c.drawString(60, y, line)
            y -= 18
        c.showPage()
    c.save()


def form_pdf(path, values, printed=()):
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(60, 740, "Survey form")
    for i, line in enumerate(printed):  # printed instructions, the way real forms carry them
        c.drawString(60, 300 - 18 * i, line)
    y = 700
    for name, val in values.items():
        c.drawString(60, y + 4, name)
        c.acroForm.textfield(name=name, value=val, x=160, y=y, width=300, height=18)
        y -= 40
    c.save()


def scan_pdf(path):
    c = canvas.Canvas(str(path), pagesize=letter)
    c.rect(50, 50, 500, 650, fill=1)  # an image-like page with no text at all
    c.showPage()
    c.save()


SPEC = {"fields": [
    {"name": "name", "labels": ["Name"], "form_names": ["full_name"]},
    {"name": "email", "labels": ["Email"], "form_names": ["email"], "pattern": r"[^@\s]+@[^@\s]+\.[a-z]{2,}"},
    {"name": "heard", "labels": ["Q1. How did you hear about us?"]},
    {"name": "comments", "labels": ["Q2. Comments"], "multiline": True},
    {"name": "rating", "labels": ["Q3. Rating"], "pattern": r"[1-5]"},
]}


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "replies"
    d.mkdir()
    text_pdf(d / "a_typed.pdf", [["Customer survey", "Name: Ana Lopez", "Email: ana@example.com",
                                  "Q1. How did you hear about us?", "A friend told me", "Q2. Comments:",
                                  "Loved the onboarding.", "Wish exports were faster.", "Q3. Rating: 4"]])
    text_pdf(d / "b_twopage.pdf", [["Name: Ben Ode", "Q1. How did you hear about us? Search"],
                                   ["Email: ben@example.com", "Q3. Rating: 5"]])
    text_pdf(d / "c_bademail.pdf", [["Name: Cy Park", "Email: cy at example dot com", "Q3. Rating: 9"]])
    form_pdf(d / "d_form.pdf", {"full_name": "Di Hart", "email": "di@example.com"})
    scan_pdf(d / "e_scan.pdf")
    text_pdf(d / "f_locked.pdf", [["Name: Secret"]], encrypt=StandardEncryption("pw", canPrint=0))
    text_pdf(d / "h_prose.pdf", [["Please enter the owner's name on line 1 before signing.", "name on line 2 if a business.", "Name: Hal Kim"]])
    text_pdf(d / "i_short.pdf", [["Q3. Rating: 3"]])
    form_pdf(d / "j_blankform.pdf", {"full_name": "", "email": ""},
             printed=["Name: print clearly, as on your ID", "Email: a work address is fine"])
    (d / "g_broken.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    (d / "notes.txt").write_text("not a pdf, must be ignored")
    return d


@pytest.fixture
def spec_file(tmp_path):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(SPEC))
    return p
