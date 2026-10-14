cd "$(dirname "$0")/.."; source docs/_say.sh; export PATH="$PWD/.venv/bin:$PATH"; clear
note "four PDF replies: typed, two-page, a fillable form, and a scan"
run "ls examples/replies"
run "pdfrows examples/replies --spec examples/fields.json --out /tmp/rows.csv"
run "cut -d, -f1-4,8,13 /tmp/rows.csv | column -s, -t"
note "the scan is reported as needing OCR instead of coming back blank"
