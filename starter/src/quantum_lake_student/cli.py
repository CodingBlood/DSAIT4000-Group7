"""Command-line entry point for the student workspace."""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.table import Table

from .config import Settings
from .connections import bronze_inventory, check_platform


console = Console()


def command_check(settings: Settings) -> int:
    result = check_platform(settings)
    for service, message in result.items():
        console.print(f"[green]OK[/green] {service}: {message}")
    return 0


def command_inventory(settings: Settings) -> int:
    table = Table(title="Supplied course data files (kept unchanged)")
    table.add_column("Stored path")
    table.add_column("Bytes", justify="right")
    for key, size in bronze_inventory(settings):
        table.add_row(key, f"{size:,}")
    console.print(table)
    return 0


def command_run(settings: Settings) -> int:
    import json
    import os
    import subprocess
    import uuid # generate id for each unique pipeline run
    from .stages import register_sources,prepare_data, load_postgres,build_ml_tables

    run_id = str(uuid.uuid4()) # create id for that pipeline run
    result1 = register_sources.run(run_id) # make silver table
    console.print(f"[green]Stage '{result1.stage}' "
                  f"{result1.output_count} rows written")
    result2 = prepare_data.run(run_id) # make silver table
    console.print(f"[green]Stage '{result2.stage}' "
                  f"{result2.output_count} rows written")
    result3 = load_postgres.run(run_id)
    console.print(f"[green]Stage '{result3.stage}'[/green]: "
                  f"{result3.output_count} rows loaded")

    result4 = build_ml_tables.run(run_id)
    console.print(f"[green]Stage '{result4.stage}'[/green]: {result4.output_count} ML rows exported")
    # manual step for actual correct hash: git rev-parse HEAD > starter/.git-revision 
    # THIS IS FOR RUN.JSON
    def git_revision() -> str:
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip()
        except Exception:
            return "unknown"
    table_counts = {}
    # tries to count the Silver rows
    try:
        import pandas as pd
        for name, path in [
            ("silver.google_qec.experiment", "silver/google_qec/experiment.parquet"),
            ("silver.google_qec.shot", "silver/google_qec/shots.parquet"),
        ]:
            if os.path.exists(path):
                table_counts[name] = len(pd.read_parquet(path))
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] could not count Silver rows: {exc}")
    # tries to count the GOld rows
    try: 
        from .connections import postgres_connection
        with postgres_connection(settings) as conn: # postgres connection
            with conn.cursor() as cur:
                for table in ["experiment", "shot", "decoder", "shot_prediction"]:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    table_counts[f"gold.{table}"] = cur.fetchone()[0] # get row count 
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] could not count Gold rows: {exc}")
    stage_results = [result1, result2, result3]
    # record the actual run information 
    run_record = {
        "run_id:": run_id,
        "code_revision: ": git_revision(),
        "started_at:": stage_results[0].started_at.isoformat(),
        "finished_at": stage_results[-1].finished_at.isoformat() if stage_results[-1].finished_at else None,
        "stages" : [
            {
                "stage": r.stage,
                "input_count": r.input_count,
                "output_count": r.output_count,
                "issue_count": r.issue_count,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in stage_results
        ], 
        "table_row_count": table_counts,
    }

    # output results
    os.makedirs("results/part1", exist_ok=True) 
    with open("results/part1/run.json", "w") as f:
        json.dump(run_record, f, indent=2)

    console.print("[green]Wrote results/part1/run.json[/green]")

    return 0


def command_train(_: Settings) -> int:
    import uuid
    from .stages import train

    model_run_id = str(uuid.uuid4())
    result = train.run(model_run_id)
    console.print(f"[green]Stage '{result.stage}'[/green]: {result.output_count} predictions written")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "command",
        choices=("check", "inventory", "run", "train"),
        help="Action to perform",
    )
    return result


def main() -> None:
    arguments = parser().parse_args()
    settings = Settings.from_environment()
    commands = {
        "check": command_check,
        "inventory": command_inventory,
        "run": command_run,
        "train": command_train,
    }
    raise SystemExit(commands[arguments.command](settings))


if __name__ == "__main__":
    main()
