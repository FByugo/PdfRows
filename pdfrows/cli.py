"""Command line entry point: `pdfrows ./replies --spec fields.json --out rows.csv`."""
from __future__ import annotations

from pathlib import Path

import typer

from .core import Spec, run

app = typer.Typer(add_completion=False, help="Turn a folder of PDFs into one CSV row per file.")


@app.command()
def main(
    folder: Path = typer.Argument(..., exists=True, file_okay=False, help="Folder of .pdf files."),
    spec: Path = typer.Option(..., "--spec", "-s", exists=True, help="JSON list of the fields to pull out."),
    out: Path = typer.Option(Path("rows.csv"), "--out", "-o"),
    strict: bool = typer.Option(False, help="Exit 1 unless every file came out complete."),
):
    """Pull the fields in SPEC out of every PDF in FOLDER."""
    try:
        summary = run(folder, Spec.load(spec), out)
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2)
    parts = ", ".join(f"{k} {v}" for k, v in sorted(summary["counts"].items()))
    typer.echo(f"{summary['files']} files: {parts}. Rows in {out}")
    if strict and set(summary["counts"]) - {"ok"}:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
