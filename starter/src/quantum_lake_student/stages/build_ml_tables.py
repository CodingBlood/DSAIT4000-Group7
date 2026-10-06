"""Create the required analyses and ML input tables from PostgreSQL Gold.

Write the Gold queries/views, joins, stable example IDs, and export logic.
A thin export step may execute SQL, use the course split helpers, validate the
fixed contracts, and write Parquet. It must not re-read Bronze or Silver.
"""

import os
import pandas as pd
from quantum_lake_student.config import Settings
from quantum_lake_student.ml import (
    google_data_split,
    syndrome_data_split,
    syndrome_model_input,
)
from quantum_lake_student.models import StageResult
from quantum_lake_student.pipeline_utils import write_parquet
from quantum_lake_student.connections import minio_client, postgres_connection

ML_DIR = "ml"
GOOGLE_ML_PATH = "ml/ml_google_decoder_example.parquet"
SYNDROME_ML_PATH = "ml/ml_syndrome_decoder_example.parquet"

GOOGLE_VIEW_SQL = """
CREATE OR REPLACE VIEW v_ml_google_decoder_example AS
SELECT
    'ml_' || left(md5(s.experiment_id || ':' || s.shot_index::text), 12) AS example_id,
    s.shot_id                                   AS shot_id,
    s.experiment_id                             AS experiment_id,
    s.shot_index                                AS shot_index,
    e.distance                                  AS distance,
    e.rounds                                    AS rounds,
    e.center_row                                AS center_row,
    e.center_col                                AS center_col,
    e.detector_count                            AS detector_count,
    s.detector_event_count                      AS detector_event_count,
    s.detector_bits                             AS detector_bits,
    bm.predicted_flip                           AS belief_matching_prediction,
    cm.predicted_flip                           AS correlated_matching_prediction,
    pm.predicted_flip                           AS pymatching_prediction,
    tn.predicted_flip                           AS tensor_network_contraction_prediction,
    s.actual_observable_flip                    AS actual_observable_flip
FROM shot s
JOIN experiment e ON e.experiment_id = s.experiment_id
JOIN shot_prediction bm ON bm.shot_id = s.shot_id AND bm.decoder_id = 'belief_matching'
JOIN shot_prediction cm ON cm.shot_id = s.shot_id AND cm.decoder_id = 'correlated_matching'
JOIN shot_prediction pm ON pm.shot_id = s.shot_id AND pm.decoder_id = 'pymatching'
JOIN shot_prediction tn ON tn.shot_id = s.shot_id AND tn.decoder_id = 'tensor_network_contraction'
"""

GOOGLE_COLUMNS = [
    "example_id", "experiment_id", "shot_index", "distance", "rounds",
    "center_row", "center_col", "detector_count", "detector_event_count",
    "detector_bits", "belief_matching_prediction", "correlated_matching_prediction",
    "pymatching_prediction", "tensor_network_contraction_prediction",
    "actual_observable_flip", "data_split",
]


def get_google_MlTables(settings: Settings) -> pd.DataFrame:
    with postgres_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute("SET max_parallel_workers_per_gather = 0")
            cur.execute(GOOGLE_VIEW_SQL)
            cur.execute("SELECT * FROM v_ml_google_decoder_example ORDER BY experiment_id, shot_index")
            columns = [d.name for d in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=columns)
        conn.commit()

    df["detector_bits"] = df["detector_bits"].apply(bytes)

    data_splits = []
    for shot_index in df["shot_index"]:
        data_splits.append(google_data_split(shot_index))
    df["data_split"] = data_splits

    df = df.astype({
        "example_id": "string",
        "experiment_id": "string",
        "shot_index": "int64",
        "distance": "int32",
        "rounds": "int32",
        "center_row": "int32",
        "center_col": "int32",
        "detector_count": "int32",
        "detector_event_count": "int32",
        "actual_observable_flip": "bool",
        "belief_matching_prediction": "bool",
        "correlated_matching_prediction": "bool",
        "pymatching_prediction": "bool",
        "tensor_network_contraction_prediction": "bool",
        "data_split": "string",
    })
    validate_google_ml_table(df)
    return df[GOOGLE_COLUMNS].reset_index(drop=True)


def validate_google_ml_table(df: pd.DataFrame) -> None:
    duplicate_count = len(df) - len(df["example_id"].drop_duplicates())
    if duplicate_count > 0:
        raise ValueError(f"example_id is not unique, {duplicate_count} duplicates found")

    bad_length_rows = []
    bad_padding_rows = []
    bad_count_rows = []
    for i in range(len(df)):
        detector_bits = df["detector_bits"][i]
        detector_count = int(df["detector_count"][i])
        expected_length = (detector_count + 7) // 8
        if len(detector_bits) != expected_length:
            bad_length_rows.append(i)
            continue
        used_bits = detector_count % 8
        if used_bits != 0 and detector_bits[-1] >> used_bits != 0:
            bad_padding_rows.append(i)
        set_bits = 0
        for value in detector_bits:
            set_bits += value.bit_count()
        if set_bits != df["detector_event_count"][i]:
            bad_count_rows.append(i)
    if len(bad_length_rows) > 0:
        raise ValueError(f"{len(bad_length_rows)} rows have wrong detector_bits length")
    if len(bad_padding_rows) > 0:
        raise ValueError(f"{len(bad_padding_rows)} rows have non-zero padding bits in detector_bits")
    if len(bad_count_rows) > 0:
        raise ValueError(f"{len(bad_count_rows)} rows have detector_event_count different from the set bits")

    found_splits = set()
    for i in range(len(df)):
        found_splits.add(df["data_split"][i])
    required_splits = {"train", "validation", "test"}
    if found_splits != required_splits:
        missing = required_splits - found_splits
        raise ValueError(f"missing data_split values: {missing}")


SYNDROME_VIEW_SQL = """
CREATE OR REPLACE VIEW v_ml_syndrome_decoder_example AS
SELECT
    md5(o.experiment_id || '|' || encode(p.syndrome_bits, 'hex') || '|'
        || (o.logical_error_label::int)::text)      AS example_id,
    o.source_record_id                              AS source_record_id,
    o.experiment_id                                 AS experiment_id,
    e.physical_fault_rate                           AS physical_fault_rate,
    p.syndrome_bits                                 AS syndrome_bits,
    p.round_count                                   AS round_count,
    p.check_count                                   AS check_count,
    o.logical_error_label                           AS logical_error_label,
    o.quantity                                      AS sample_weight
FROM syndrome_observation o
JOIN syndrome_pattern    p ON p.pattern_id    = o.pattern_id
JOIN syndrome_experiment e ON e.experiment_id = o.experiment_id
"""
 
SYNDROME_COLUMNS = [
    "example_id", "experiment_id", "physical_fault_rate", "syndrome_bits",
    "round_count", "check_count", "logical_error_label", "sample_weight",
    "data_split",
]
 
 
def get_syndrome_MlTable(settings: Settings) -> pd.DataFrame:
    with postgres_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute("SET max_parallel_workers_per_gather = 0")
            cur.execute(SYNDROME_VIEW_SQL)
            cur.execute("SELECT * FROM v_ml_syndrome_decoder_example")
            columns = [d.name for d in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=columns)
        conn.commit()
 
    df["syndrome_bits"] = df["syndrome_bits"].apply(bytes)
    df["data_split"] = df["physical_fault_rate"].map(syndrome_data_split)
    df = df.astype({
        "example_id": "string",
        "experiment_id": "string",
        "physical_fault_rate": "float64",
        "round_count": "int32",
        "check_count": "int32",
        "logical_error_label": "bool",
        "sample_weight": "int64",
        "data_split": "string",
    })
    df = df.sort_values("example_id").reset_index(drop=True)
    validate_syndrome_ml_table(df)
    return df[SYNDROME_COLUMNS]
 
 
def validate_syndrome_ml_table(df: pd.DataFrame) -> None:
    if not df["example_id"].is_unique:
        raise ValueError("ml_syndrome_decoder_example: example_id values are not unique")
    present = set(df["data_split"].unique())
    if present != {"train", "validation", "test"}:
        raise ValueError(f"ml_syndrome_decoder_example: bad splits {sorted(present)}")
    for bits in df["syndrome_bits"]:
        syndrome_model_input(bits)
    if not (df["round_count"] == 4).all() or not (df["check_count"] == 4).all():
        raise ValueError("ml_syndrome_decoder_example: round_count and check_count must be 4")
    if not (df["sample_weight"] > 0).all():
        raise ValueError("ml_syndrome_decoder_example: sample_weight must be positive")

        
def run(run_id: str) -> StageResult:
    result = StageResult(stage="build_ml_tables", run_id=run_id)
    settings = Settings.from_environment()

    os.makedirs(ML_DIR, exist_ok=True)

    df_google_ml = get_google_MlTables(settings)
    write_parquet(df_google_ml, GOOGLE_ML_PATH)

    df_syndrome_ml = get_syndrome_MlTable(settings)
    write_parquet(df_syndrome_ml, SYNDROME_ML_PATH)

    client = minio_client(settings)
    for path in [GOOGLE_ML_PATH, SYNDROME_ML_PATH]:
        client.fput_object(bucket_name=settings.s3_bucket, object_name=path, file_path=path)

    total = len(df_google_ml) + len(df_syndrome_ml)
    result.input_count = total
    result.output_count = total
    result.issue_count = 0
    result.finish()
    return result