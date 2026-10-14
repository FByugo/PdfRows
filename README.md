# pdfrows

You have a folder of PDFs. Survey replies, intake forms, applications. You want one spreadsheet with a row per file.

```
pdfrows ./replies --spec fields.json --out rows.csv
```

## The spec

```json
{"fields": [
 {"name": "name", "labels": ["Name"], "form_names": ["full_name"]},
 {"name": "email", "labels": ["Email"], "pattern": "[^@\\s]+@[^@\\s]+\\.[a-z]{2,}"},
 {"name": "comments", "labels": ["Q2. Comments"], "multiline": true}
]}
```

If the PDF is a fillable form, the value comes straight from the field named in `form_names`. Nothing is guessed.

If it's a typed or flattened PDF, the value is whatever follows the label, on the same line or the next. A label has to start its line and match the way it's written, so "Name" doesn't grab a sentence like "enter the owner's name on line 1". `multiline` keeps reading until the next label.

`pattern` checks the value. A value that fails is kept as written and listed under `invalid`, not thrown away.

## What every row tells you

Each value has a `_source` column (`form` or `page 2`), so you can check it.

`missing` names the fields that weren't found.

`status` is one of these:

| status | meaning |
|---|---|
| ok | everything found |
| partial | some fields missing or invalid, see the columns |
| needs_ocr | no text in it at all, it's a scan |
| encrypted | password protected, not opened |
| unreadable | broken file |

On a fillable form, an empty field is reported as empty. The printed instructions next to it are never read as the answer. (Tried on a blank IRS W-9 before that rule existed, it returned "An entry is required" as someone's name.)

## Tests

`./check.sh` builds its own test PDFs: typed, two-page, fillable, blank fillable with printed instructions, scanned, password locked, broken, and one where the label word shows up inside a sentence. Then it runs 13 tests on them.

Also checked by hand on the real IRS W-9, blank and with a field filled in.
