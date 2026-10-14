cd "$(dirname "$0")/.."; source docs/_say.sh; export PATH="$PWD/.venv/bin:$PATH"; clear
note "four PDF replies: typed, two-page, a fillable form, and a scan"
run "ls examples/replies"
run "pdfrows examples/replies --spec examples/fields.json --out /tmp/rows.csv"
run "python -c \"import csv;[print(r['file'].ljust(18), r['status'].ljust(9), r['name'].ljust(10), r['email'].ljust(16), r['name_source'] or '-', '|', r['missing'] or '-') for r in csv.DictReader(open('/tmp/rows.csv'))]\""
note "the scan is reported as needing OCR instead of coming back blank"
