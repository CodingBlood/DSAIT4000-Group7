import json
import os

import pandas as pd

from quantum_lake_student.config import Settings
from quantum_lake_student.connections import postgres_connection

GOOGLE_EXPERIMENT_ID = "google_surface_code_bX_d3_r25_center_3_5"
GOOGLE_SHOT_INDEX = 1
SYNDROME_TEST_FAULT_RATE = 0.005

GOOGLE_ML_PATH = "ml/ml_google_decoder_example.parquet"
SYNDROME_ML_PATH = "ml/ml_syndrome_decoder_example.parquet"
PREDICTIONS_PATH = "results/part2/predictions.parquet"
SHOT_SILVER_PATH = "silver/google_qec/shot.parquet"
EXPERIMENT_SILVER_PATH = "silver/google_qec/experiment.parquet"
SYNDROME_SILVER_PATH = "silver/qec_syndromes/syndrome_observation.parquet"
TRACE_PATH = "results/part1/source_trace.parquet"


def query_one(cur, sql: str, params: tuple) -> dict:
    cur.execute(sql, params)
    columns = [d.name for d in cur.description]
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"No Gold row found for {params}")
    result = {}
    for i in range(len(columns)):
        result[columns[i]] = row[i]
    return result


def query_all(cur, sql: str, params: tuple) -> list[dict]:
    cur.execute(sql, params)
    columns = [d.name for d in cur.description]
    rows = []
    for row in cur.fetchall():
        record = {}
        for i in range(len(columns)):
            record[columns[i]] = row[i]
        rows.append(record)
    return rows


def parquet_rows(path: str, column: str, value) -> list[dict]:
    df = pd.read_parquet(path)
    return df[df[column] == value].to_dict(orient="records")


def prediction_rows(example_id: str) -> list[dict]:
    if not os.path.exists(PREDICTIONS_PATH):
        return []
    return parquet_rows(PREDICTIONS_PATH, "example_id", example_id)


def build_google_trace(cur) -> dict:
    gold_example = query_one(
        cur,
        "SELECT example_id, shot_id, experiment_id, shot_index FROM v_ml_google_decoder_example "
        "WHERE experiment_id = %s AND shot_index = %s",
        (GOOGLE_EXPERIMENT_ID, GOOGLE_SHOT_INDEX),
    )
    example_id = gold_example["example_id"]
    shot_id = gold_example["shot_id"]

    gold_shot = query_one(
        cur,
        "SELECT shot_id, source_record_id, experiment_id, shot_index, detector_event_count, "
        "actual_observable_flip FROM shot WHERE shot_id = %s",
        (shot_id,),
    )
    gold_experiment = query_one(
        cur,
        "SELECT experiment_id, source_record_id, distance, rounds, center_row, center_col "
        "FROM experiment WHERE experiment_id = %s",
        (gold_shot["experiment_id"],),
    )
    gold_predictions = query_all(
        cur,
        "SELECT decoder_id, predicted_flip FROM shot_prediction WHERE shot_id = %s ORDER BY decoder_id",
        (shot_id,),
    )

    shot_record_id = gold_shot["source_record_id"]
    experiment_record_id = gold_experiment["source_record_id"]

    return {
        "part2_predictions": prediction_rows(example_id),
        "ml": {
            "table": GOOGLE_ML_PATH,
            "rows": parquet_rows(GOOGLE_ML_PATH, "example_id", example_id),
        },
        "gold": {
            "resolved_by": "v_ml_google_decoder_example",
            "view_row": gold_example,
            "shot": gold_shot,
            "experiment": gold_experiment,
            "shot_prediction": gold_predictions,
        },
        "silver": {
            "shot_table": SHOT_SILVER_PATH,
            "shot_rows": parquet_rows(SHOT_SILVER_PATH, "source_record_id", shot_record_id),
            "experiment_table": EXPERIMENT_SILVER_PATH,
            "experiment_rows": parquet_rows(EXPERIMENT_SILVER_PATH, "source_record_id", experiment_record_id),
        },
        "bronze": {
            "shot_trace_rows": parquet_rows(TRACE_PATH, "source_record_id", shot_record_id),
            "experiment_trace_rows": parquet_rows(TRACE_PATH, "source_record_id", experiment_record_id),
        },
    }


def build_syndrome_trace(cur) -> dict:
    gold_example = query_one(
        cur,
        "SELECT example_id, source_record_id, experiment_id, physical_fault_rate "
        "FROM v_ml_syndrome_decoder_example WHERE physical_fault_rate = %s ORDER BY example_id LIMIT 1",
        (SYNDROME_TEST_FAULT_RATE,),
    )
    example_id = gold_example["example_id"]
    record_id = gold_example["source_record_id"]

    gold_observation = query_one(
        cur,
        "SELECT source_record_id, experiment_id, pattern_id, logical_error_label, quantity "
        "FROM syndrome_observation WHERE source_record_id = %s",
        (record_id,),
    )
    gold_pattern = query_one(
        cur,
        "SELECT pattern_id, syndrome_bits, round_count, check_count FROM syndrome_pattern WHERE pattern_id = %s",
        (gold_observation["pattern_id"],),
    )
    gold_experiment = query_one(
        cur,
        "SELECT experiment_id, distance, physical_fault_rate FROM syndrome_experiment WHERE experiment_id = %s",
        (gold_observation["experiment_id"],),
    )

    return {
        "part2_predictions": prediction_rows(example_id),
        "ml": {
            "table": SYNDROME_ML_PATH,
            "rows": parquet_rows(SYNDROME_ML_PATH, "example_id", example_id),
        },
        "gold": {
            "resolved_by": "v_ml_syndrome_decoder_example",
            "view_row": gold_example,
            "syndrome_observation": gold_observation,
            "syndrome_pattern": gold_pattern,
            "syndrome_experiment": gold_experiment,
        },
        "silver": {
            "table": SYNDROME_SILVER_PATH,
            "rows": parquet_rows(SYNDROME_SILVER_PATH, "source_record_id", record_id),
        },
        "bronze": {
            "trace_rows": parquet_rows(TRACE_PATH, "source_record_id", record_id),
        },
    }


def to_json_value(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def main():
    settings = Settings.from_environment()
    with postgres_connection(settings) as conn:
        with conn.cursor() as cur:
            google_trace = build_google_trace(cur)
            syndrome_trace = build_syndrome_trace(cur)

    record = {
        "google_trace": google_trace,
        "syndrome_trace": syndrome_trace,
    }

    os.makedirs("results/part1", exist_ok=True)
    with open("results/part1/trace_examples.json", "w") as f:
        json.dump(record, f, indent=2, default=to_json_value)
    print("Wrote results/part1/trace_examples.json")


if __name__ == "__main__":
    main()
