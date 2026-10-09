import csv
import os

from quantum_lake_student.config import Settings
from quantum_lake_student.connections import postgres_connection
from quantum_lake_student.models import StageResult
from quantum_lake_student.pipeline_utils import PipelineError

SQL_DIR = "sql/analysis"
OUTPUT_DIR = "results/part1/analysis"


def run(run_id: str) -> StageResult:
    result = StageResult(stage="analysis", run_id=run_id)
    settings = Settings.from_environment()

    if not os.path.isdir(SQL_DIR):
        raise PipelineError(f"Analysis folder {SQL_DIR} not found. Run the pipeline from the starter folder.")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    sql_files = []
    for file_name in sorted(os.listdir(SQL_DIR)):
        if file_name.endswith(".sql"):
            sql_files.append(file_name)

    with postgres_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute("SET max_parallel_workers_per_gather = 0")
            for file_name in sql_files:
                with open(os.path.join(SQL_DIR, file_name)) as f:
                    sql = f.read()
                cur.execute(sql)
                columns = [d.name for d in cur.description]
                rows = cur.fetchall()

                output_path = os.path.join(OUTPUT_DIR, file_name.replace(".sql", ".csv"))
                with open(output_path, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(columns)
                    writer.writerows(rows)
                print(f"Wrote {output_path} ({len(rows)} rows)")
                result.output_count += len(rows)

    result.input_count = len(sql_files)
    result.issue_count = 0
    result.finish()
    return result
