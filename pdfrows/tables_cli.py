"""Command line entry point: `pdftables ./pdfs --out tables.csv`."""
from __future__ import annotations

from pathlib import Path

import typer

from .tables import run_tables

app = typer.Typer(add_completion=False, help="Pull every table out of a folder of PDFs into one sheet.")


@app.command()
def main(
    folder: Path = typer.Argument(..., exists=True, file_okay=False, help="Folder of .pdf files."),
    out: Path = typer.Option(Path("tables.csv"), "--out", "-o", help="The sheet. A report is written next to it."),
    source_columns: bool = typer.Option(False, help="Put the file name and page in front of every row."),
    join_pages: bool = typer.Option(True, help="Treat a table that repeats its header on the next page as one table."),
    strict: bool = typer.Option(False, help="Exit 1 if any file had a problem or any total did not add up."),
):
    """Read every table in FOLDER, in file and page order."""
    try:
        s = run_tables(folder, out, join_pages=join_pages, source_columns=source_columns)
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2)
    n = lambda k, word: f"{k} {word}" + ("" if k == 1 else "s")
    typer.echo(f"{n(s['files'], 'file')}, {n(s['tables'], 'table')}, {n(s['rows'], 'row')} in {out}")
    if s["by_spacing"]:
        typer.echo(f"{n(s['by_spacing'], 'table')} had no ruled lines and came from spacing, look there first")
    if s["total_mismatches"]:
        typer.echo(f"{n(s['total_mismatches'], 'total')} did not add up from the rows above")
    for p in s["problem_list"]:
        typer.echo(f"problem: {p.file}{' page ' + str(p.page) if p.page else ''}: {p.what}")
    typer.echo(f"Report in {s['report']}")
    if strict and (s["problem_list"] or s["total_mismatches"]):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
