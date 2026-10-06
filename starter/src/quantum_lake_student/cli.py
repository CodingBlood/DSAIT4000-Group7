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


PARQUET_TABLES = {
    "silver.google_qec.experiment": "silver/google_qec/experiment.parquet",
    "silver.google_qec.shot": "silver/google_qec/shot.parquet",
    "silver.qec_syndromes.syndrome_observation": "silver/qec_syndromes/syndrome_observation.parquet",
    "silver.qasmbench.circuit": "silver/qasmbench/circuit.parquet",
    "silver.qasmbench.stabilizer_check": "silver/qasmbench/stabilizer_check.parquet",
    "silver.qasmbench.conditional_correction": "silver/qasmbench/conditional_correction.parquet",
    "ml.ml_google_decoder_example": "ml/ml_google_decoder_example.parquet",
    "ml.ml_syndrome_decoder_example": "ml/ml_syndrome_decoder_example.parquet",
}
GOLD_TABLES = [
    "experiment", "shot", "decoder", "shot_prediction",
    "syndrome_experiment", "syndrome_pattern", "syndrome_observation",
    "circuit", "circuit_register", "circuit_qubit",
    "stabilizer_check", "stabilizer_check_data_qubit", "conditional_correction",
]


def readable_error(settings: Settings, error: Exception) -> str | None:
    import psycopg
    from minio.error import S3Error
    from urllib3.exceptions import MaxRetryError
    from .pipeline_utils import PipelineError

    if isinstance(error, PipelineError):
        return str(error)
    if isinstance(error, psycopg.OperationalError):
        return (f"PostgreSQL error at {settings.postgres_host}:{settings.postgres_port}: {error}. "
                "If the database is not running, start the platform with make up.")
    if isinstance(error, MaxRetryError):
        return f"Cannot connect to MinIO at {settings.s3_endpoint}. Start the platform with make up and try again."
    if isinstance(error, FileNotFoundError):
        return f"File not found: {error.filename}. Run the earlier pipeline stages first."
    if isinstance(error, S3Error):
        return f"MinIO error {error.code} for {error.object_name}. Run make seed to load the Bronze files."
    return None


def git_revision() -> str:
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def run_pipeline(settings: Settings) -> None:
    import json
    import os
    import uuid
    import pyarrow.parquet as pq
    from .connections import postgres_connection
    from .stages import register_sources, prepare_data, load_postgres, build_ml_tables, analysis
    from .traces import build_trace_examples

    run_id = str(uuid.uuid4())
    result1 = register_sources.run(run_id)
    console.print(f"[green]Stage '{result1.stage}'[/green]: {result1.output_count} Bronze objects verified")
    result2 = prepare_data.run(run_id)
    console.print(f"[green]Stage '{result2.stage}'[/green]: {result2.output_count} Silver rows written")
    result3 = load_postgres.run(run_id)
    console.print(f"[green]Stage '{result3.stage}'[/green]: {result3.output_count} rows loaded")
    result4 = build_ml_tables.run(run_id)
    console.print(f"[green]Stage '{result4.stage}'[/green]: {result4.output_count} ML rows exported")
    result5 = analysis.run(run_id)
    console.print(f"[green]Stage '{result5.stage}'[/green]: {result5.input_count} SQL analyses saved")
    build_trace_examples.main()

    stage_results = [result1, result2, result3, result4, result5]

    table_counts = {}
    for name, path in PARQUET_TABLES.items():
        table_counts[name] = pq.read_metadata(path).num_rows

    with postgres_connection(settings) as conn:
        with conn.cursor() as cur:
            for table in GOLD_TABLES:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                table_counts[f"gold.{table}"] = cur.fetchone()[0]

    stages = []
    for r in stage_results:
        finished = None
        if r.finished_at:
            finished = r.finished_at.isoformat()
        stages.append({
            "stage": r.stage,
            "input_count": r.input_count,
            "output_count": r.output_count,
            "issue_count": r.issue_count,
            "started_at": r.started_at.isoformat(),
            "finished_at": finished,
        })

    run_record = {
        "run_id": run_id,
        "code_revision": git_revision(),
        "input_hashes": result1.input_hashes,
        "started_at": stage_results[0].started_at.isoformat(),
        "finished_at": stages[-1]["finished_at"],
        "stages": stages,
        "table_row_count": table_counts,
    }

    os.makedirs("results/part1", exist_ok=True)
    with open("results/part1/run.json", "w") as f:
        json.dump(run_record, f, indent=2)
    console.print("[green]Wrote results/part1/run.json[/green]")


def command_run(settings: Settings) -> int:
    try:
        run_pipeline(settings)
    except Exception as error:
        message = readable_error(settings, error)
        if message is None:
            raise
        console.print(f"[red]Pipeline stopped:[/red] {message}")
        return 1
    return 0


def snapshot(settings: Settings) -> dict:
    import hashlib
    import pyarrow.parquet as pq
    from .connections import postgres_connection

    tables = {}
    for name, path in PARQUET_TABLES.items():
        id_column = "source_record_id"
        if name.startswith("ml."):
            id_column = "example_id"
        ids = pq.read_table(path, columns=[id_column]).column(0).to_pylist()
        ids.sort()
        tables[name] = {
            "rows": len(ids),
            "id_hash": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
        }

    with postgres_connection(settings) as conn:
        with conn.cursor() as cur:
            for table in GOLD_TABLES:
                cur.execute(
                    f"SELECT COUNT(*), md5(string_agg(md5(t::text), '' ORDER BY md5(t::text))) FROM {table} t"
                )
                row = cur.fetchone()
                tables[f"gold.{table}"] = {"rows": row[0], "content_hash": row[1]}
    return tables


def command_rerun_check(settings: Settings) -> int:
    import json
    import os

    snapshots = []
    for run_number in [1, 2]:
        console.print(f"[bold]Run {run_number} of 2[/bold]")
        exit_code = command_run(settings)
        if exit_code != 0:
            return exit_code
        snapshots.append(snapshot(settings))

    changed = []
    for name in snapshots[0]:
        if snapshots[0][name] != snapshots[1][name]:
            changed.append(name)

    evidence = {
        "code_revision": git_revision(),
        "identical_after_second_run": len(changed) == 0,
        "changed_tables": changed,
        "first_run": snapshots[0],
        "second_run": snapshots[1],
    }
    os.makedirs("results/part1", exist_ok=True)
    with open("results/part1/rerun_check.json", "w") as f:
        json.dump(evidence, f, indent=2)

    if len(changed) > 0:
        console.print(f"[red]Second run changed: {changed}[/red]")
        return 1
    console.print("[green]Second run produced identical rows and identifiers. Wrote results/part1/rerun_check.json[/green]")
    return 0


def command_train(settings: Settings) -> int:
    import uuid
    from .stages import train
    from .traces import build_trace_examples

    try:
        model_run_id = str(uuid.uuid4())
        result = train.run(model_run_id)
        console.print(f"[green]Stage '{result.stage}'[/green]: {result.output_count} predictions written")
        build_trace_examples.main()
    except Exception as error:
        message = readable_error(settings, error)
        if message is None:
            raise
        console.print(f"[red]Training stopped:[/red] {message}")
        return 1
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "command",
        choices=("check", "inventory", "run", "rerun-check", "train"),
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
        "rerun-check": command_rerun_check,
        "train": command_train,
    }
    raise SystemExit(commands[arguments.command](settings))


if __name__ == "__main__":
    main()
