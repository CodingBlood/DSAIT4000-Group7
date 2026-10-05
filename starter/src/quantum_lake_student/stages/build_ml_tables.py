"""Create the required analyses and ML input tables from PostgreSQL Gold.

Write the Gold queries/views, joins, stable example IDs, and export logic.
A thin export step may execute SQL, use the course split helpers, validate the
fixed contracts, and write Parquet. It must not re-read Bronze or Silver.
"""

import hashlib
import os
import nupmy as np
import pandas as pd
from quantum_lake_student.config import Settings
from quantum_lake_student.connections import postgres_connection
from quantum_lake_student.ml import (
    MODEL_SPLITS,
    google_data_split,
    syndrome_data_split,
    syndrome_model_input,
)
from quantum_lake_student.models import StageResult
GOOGLE_QUERY = """
SELECT
    s.source_record_id,
    s.experiment_id,
    s.shot_index,
    e.distance,
    e.rounds,
    e.center_row,
    e.center_col,
    e.detector_count,
    s.detector_event_count,
    s.detector_bits,
    s.actual_observable_flip,
    d.decoder_id,
    sp.predicted_flip
FROM shot s
JOIN experiment e ON e.experiment_id = s.experiment_id
JOIN shot_prediction sp ON sp.shot_id = s.shot_id
JOIN decoder d ON d.decoder_id = sp.decoder_id
"""
DECODER_NAMES = {
    "belief_matching": "belief_matching_prediction",
    "correlated_matching": "correlated_matching_prediction",
    "pymatching": "pymatching_prediction",
    "tensor_network_contraction": "tensor_network_contraction_prediction",
}

def get_google_MlTables(settings: Settings) -> pd.DataFrame:
    with postgres_connection(settings) as conn:
        raw = pd.read_sql(GOOGLE_QUERY, conn)

    shot_columns = [
        "source_record_id", "experiment_id", "shot_index", "distance", "rounds",
        "center_row", "center_col", "detector_count", "detector_event_count",
        "detector_bits", "actual_observable_flip",
    ]
    df = raw[shot_columns].drop_duplicates("source_record_id").reset_index(drop=True)

    for decoder_id, column_name in DECODER_NAMES.items():
        decoder_rows = raw[raw["decoder_id"] == decoder_id][["source_record_id", "predicted_flip"]]
        decoder_rows = decoder_rows.rename(columns={"predicted_flip": column_name})
        df = df.merge(decoder_rows, on="source_record_id")

    example_ids = []
    data_splits = []
    for i in range(len(df)):
        experiment_id = df["experiment_id"][i]
        shot_index = df["shot_index"][i]

        key = str(experiment_id) + ":" + str(shot_index)
        hashed = hashlib.md5(key.encode()).hexdigest()
        example_ids.append("ml_" + hashed[:12])

        data_splits.append(google_data_split(shot_index))

    df["example_id"] = example_ids
    df["data_split"] = data_splits

    validate_google_ml_table(df)

    ml_columns = [
        "example_id", "experiment_id", "shot_index", "distance", "rounds",
        "center_row", "center_col", "detector_count", "detector_event_count",
        "detector_bits", "belief_matching_prediction", "correlated_matching_prediction",
        "pymatching_prediction", "tensor_network_contraction_prediction",
        "actual_observable_flip", "data_split",
    ]
    return df[ml_columns].reset_index(drop=True)

def validate_google_ml_table(df: pd.DataFrame) -> None:
    duplicate_count = len(df) - len(df["example_id"].drop_duplicates())
    if duplicate_count > 0:
        raise ValueError(f"example_id is not unique, {duplicate_count} duplicates found")

    bad_length_rows = []
    for i in range(len(df)):
        detector_bits = df["detector_bits"][i]
        detector_count = df["detector_count"][i]
        expected_length = (detector_count + 7) // 8
        if len(detector_bits) != expected_length:
            bad_length_rows.append(i)
    if len(bad_length_rows) > 0:
        raise ValueError(f"{len(bad_length_rows)} rows have wrong detector_bits length")

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
    # raise NotImplementedError("Implement analysis outputs and ML input tables")
    result = StageResult(stage="build_ml_tables", run_id=run_id)
    settings = Settings.from_environment()

    os.makedirs("results/part2/ml", exist_ok=True)

    df_google_ml = get_google_MlTables(settings)
    df_google_ml.to_parquet("results/part2/ml/ml_google_decoder_example.parquet", index=False)


    df_syndrome_ml = get_syndrome_MlTable(settings)
    df_syndrome_ml.to_parquet("results/part2/ml/ml_syndrome_decoder_example.parquet", index=False)

    
    result.input_count = len(df_google_ml)
    result.output_count = len(df_google_ml)
    result.issue_count = 0
    result.finish()
    return result
