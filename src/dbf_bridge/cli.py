"""
dbf_bridge.cli
==============

Fasada CLI dla pakietu dbf_bridge — rekurencyjnie eksportuje pliki DBF
(wraz z powiązanymi FPT/CDX) do CSV, JSON i JSONL, zachowując strukturę
katalogów. Wykorzystuje streamingowy, atomowy eksporter z walidacją
SHA-256 i automatycznym fallback polskich stron kodowych (cp1250/cp852/Mazovia).

Punkty wejścia (instalowane przez pip):
    dbf-bridge        — eksport DBF -> CSV/JSON/JSONL
    dbf-bridge-verify — weryfikacja konwersji (dbf_bridge.verifier)

Uruchamianie z PyCharm:
    Skrypt ma domyślne wartości argumentów (DEFAULTS), więc można go uruchomić
    klikając „Run" bez konfigurowania parametrów.

Domyślnie:
    source   = tests/fixtures/synthetic_data/input
    output   = tests/fixtures/synthetic_data/output
    formats  = csv,json,jsonl
    memo     = skip dla csv, inline dla json/jsonl
    overwrite = True
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dbf_bridge.exporter.config import ConfigError, make_config
from dbf_bridge.exporter.discovery import discover_tables
from dbf_bridge.exporter.models import TableResult
from dbf_bridge.exporter.reporting import exit_code, write_reports
from dbf_bridge.exporter.writer import export_table


PROJECT_DIR = Path(__file__).resolve().parents[2]

DEFAULTS = {
    "source": PROJECT_DIR / "tests" / "fixtures" / "input",
    "output": PROJECT_DIR / "tests" / "fixtures" / "output",
    "formats": "csv,json,jsonl",
    "memo": None,
    "strip_spaces": False,
    "encoding": "auto",
    "decode_errors": "strict",
    "deleted": "skip",
    "missing_memo": "fail",
    "overwrite": True,
    "validate": True,
}

DEFAULT_MEMO_POLICY: dict[str, str] = {
    "csv": "skip",
    "json": "inline",
    "jsonl": "inline",
}

ALL_FORMATS: tuple[str, ...] = ("csv", "json", "jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbf-bridge",
        description=(
            "Rekurencyjnie eksportuje pliki DBF do CSV, JSON i JSONL z zachowaniem "
            "struktury katalogów. Fasada nad pakietem dbf_bridge.exporter."
        ),
    )
    parser.add_argument(
        "--source", type=Path, default=DEFAULTS["source"],
        help=f"Katalog źródłowy DBF (domyślnie: {DEFAULTS['source']}).",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULTS["output"],
        help=f"Katalog wyjściowy (domyślnie: {DEFAULTS['output']}).",
    )
    parser.add_argument(
        "--formats", default=DEFAULTS["formats"],
        help=f"Lista formatów rozdzielona przecinkami (domyślnie: {DEFAULTS['formats']}).",
    )
    parser.add_argument(
        "--memo", choices=["skip", "inline", "null"], default=DEFAULTS["memo"],
        help="Polityka pól memo. Domyślnie: skip dla CSV, inline dla JSON/JSONL.",
    )
    parser.add_argument(
        "--strip-spaces", action=argparse.BooleanOptionalAction,
        default=DEFAULTS["strip_spaces"],
        help="Usuń końcowe spacje z pól Character (C).",
    )
    parser.add_argument(
        "--encoding", default=DEFAULTS["encoding"],
        help="Strona kodowa DBF lub 'auto' (wykrywanie z nagłówka).",
    )
    parser.add_argument(
        "--decode-errors", choices=["strict", "ignore", "replace"],
        default=DEFAULTS["decode_errors"],
        help="Polityka błędów dekodowania znaków.",
    )
    parser.add_argument(
        "--deleted", choices=["skip", "separate", "include"],
        default=DEFAULTS["deleted"],
        help="Polityka usuniętych rekordów DBF.",
    )
    parser.add_argument(
        "--missing-memo", choices=["fail", "null-with-warning"],
        default=DEFAULTS["missing_memo"],
        help="Polityka dla tabel DBF bez pliku memo FPT.",
    )
    parser.add_argument(
        "--overwrite", action=argparse.BooleanOptionalAction,
        default=DEFAULTS["overwrite"],
        help="Nadpisz istniejące pliki wyjściowe (domyślnie: włączone).",
    )
    parser.add_argument(
        "--no-validate", dest="validate", action="store_false",
        default=DEFAULTS["validate"],
        help="Pomiń walidację SHA-256 i round-trip wyjścia.",
    )
    return parser


def _resolve_formats(formats_arg: str) -> list[str]:
    requested = [f.strip().lower() for f in formats_arg.split(",") if f.strip()]
    if not requested:
        return list(ALL_FORMATS)
    invalid = [f for f in requested if f not in ALL_FORMATS]
    if invalid:
        raise ValueError(f"Nieobsługiwany format(y): {invalid}. Dostępne: {list(ALL_FORMATS)}")
    seen: set[str] = set()
    ordered: list[str] = []
    for f in requested:
        if f not in seen:
            ordered.append(f)
            seen.add(f)
    return ordered


def _resolve_memo_policy(fmt: str, memo_arg: str | None) -> str:
    if memo_arg is not None:
        return memo_arg
    return DEFAULT_MEMO_POLICY.get(fmt, "inline")


def _export_one(
    source: Path, output: Path, fmt: str, memo: str, *,
    strip_spaces: bool, encoding: str, decode_errors: str,
    deleted: str, missing_memo: str, validate: bool, overwrite: bool,
) -> tuple[int, list[TableResult]]:
    try:
        config = make_config(
            source=source, output=output, export_format=fmt,  # type: ignore[arg-type]
            encoding=encoding, decode_errors=decode_errors,  # type: ignore[arg-type]
            deleted=deleted,  # type: ignore[arg-type]
            missing_memo=missing_memo,  # type: ignore[arg-type]
            memo=memo,  # type: ignore[arg-type]
            strip_spaces=strip_spaces, validate=validate, overwrite=overwrite,
        )
    except ConfigError as exc:
        print(f"[dbf-bridge] Błąd konfiguracji: {exc}", file=sys.stderr)
        return 1, []

    print(f"[dbf-bridge] Eksport {fmt.upper()} (memo={memo}) -> {config.output}")
    tables = discover_tables(config.source)
    if not tables:
        print(f"[dbf-bridge] Nie znaleziono plików DBF w: {config.source}")
        return 0, []

    print(f"[dbf-bridge] Znaleziono {len(tables)} plik(ów) DBF.")
    results = [export_table(table, config) for table in tables]
    write_reports(config.output, results)
    return exit_code(results), results


def _print_summary(results: list[TableResult]) -> None:
    if not results:
        return
    ok = sum(1 for r in results if r.status == "OK")
    warning = sum(1 for r in results if r.status == "WARNING")
    failed = sum(1 for r in results if r.status in {"FAILED", "UNSUPPORTED"})
    print(f"  -> OK: {ok}  Ostrzeżenia: {warning}  Błędy: {failed}")
    for r in results:
        if r.status in {"FAILED", "UNSUPPORTED"} or r.errors:
            print(f"      - {r.table}: {r.status} | {'; '.join(r.errors) if r.errors else ''}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.source.is_dir():
        print(f"[dbf-bridge] Błąd: katalog źródłowy nie istnieje: {args.source}", file=sys.stderr)
        return 1

    args.output.mkdir(parents=True, exist_ok=True)

    try:
        formats = _resolve_formats(args.formats)
    except ValueError as exc:
        print(f"[dbf-bridge] Błąd: {exc}", file=sys.stderr)
        return 1

    print(f"[dbf-bridge] Source:   {args.source}")
    print(f"[dbf-bridge] Output:   {args.output}")
    print(f"[dbf-bridge] Formats:  {', '.join(formats)}")
    print(f"[dbf-bridge] Overwrite: {args.overwrite}")
    print()

    overall_errors = 0
    for fmt in formats:
        memo = _resolve_memo_policy(fmt, args.memo)
        code, results = _export_one(
            source=args.source, output=args.output, fmt=fmt, memo=memo,
            strip_spaces=args.strip_spaces, encoding=args.encoding,
            decode_errors=args.decode_errors, deleted=args.deleted,
            missing_memo=args.missing_memo, validate=args.validate,
            overwrite=args.overwrite,
        )
        overall_errors = max(overall_errors, code)
        _print_summary(results)

    print(f"\n[dbf-bridge] Zakończono. Kod wyjścia: {overall_errors}")
    return overall_errors


if __name__ == "__main__":
    sys.exit(main())