"""Run the committed Part I analysis queries against Gold.

Every .sql file in sql/analysis/ is executed and its result written to
results/part1/analysis/<name>.csv, so the analyses are reproducible from
committed SQL rather than from something typed into a database client.

Teammates: drop q1_*.sql and q2_*.sql into the same directory and they get
picked up with no change here.

    python -m quantum_lake_student.analysis.run_analyses
    python -m quantum_lake_student.analysis.run_analyses --schema q3_verify
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from ..config import Settings
from ..connections import postgres_connection

SQL_DIRECTORY = Path("sql/analysis")
OUTPUT_DIRECTORY = Path("results/part1/analysis")


def find_query_files(directory: Path) -> list[Path]:
    """Committed .sql files, in a stable order so reruns match."""
    if not directory.is_dir():
        raise FileNotFoundError(f"{directory} not found; run from the workspace root")
    return sorted(directory.glob("*.sql"))


def write_csv(path: Path, column_names: list[str], rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(column_names)
        for row in rows:
            writer.writerow(row)


def run_one_query(cursor, query_file: Path, output_directory: Path) -> int:
    """Execute one query file and write its rows. Returns the row count."""
    cursor.execute(query_file.read_text(encoding="utf-8"))
    column_names = []
    for description in cursor.description:
        column_names.append(description.name)
    rows = cursor.fetchall()
    write_csv(output_directory / f"{query_file.stem}.csv", column_names, rows)
    return len(rows)


def run(schema: str | None = None) -> dict[str, int]:
    settings = Settings.from_environment()
    query_files = find_query_files(SQL_DIRECTORY)
    counts = {}

    with postgres_connection(settings) as connection:
        with connection.cursor() as cursor:
            if schema is not None:
                cursor.execute(f"SET search_path TO {schema}")
            for query_file in query_files:
                counts[query_file.name] = run_one_query(
                    cursor, query_file, OUTPUT_DIRECTORY
                )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        default=None,
        help="Schema to run against. Defaults to the connection's search_path.",
    )
    arguments = parser.parse_args()

    counts = run(arguments.schema)
    if not counts:
        print(f"No .sql files found in {SQL_DIRECTORY}")
        return
    for name in counts:
        print(f"{name:40} {counts[name]:>5} rows")
    print(f"\nWrote {len(counts)} result file(s) to {OUTPUT_DIRECTORY}")


if __name__ == "__main__":
    main()
