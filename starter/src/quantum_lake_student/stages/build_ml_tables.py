"""Create the required analyses and ML input tables from PostgreSQL Gold.

Write the Gold queries/views, joins, stable example IDs, and export logic.
A thin export step may execute SQL, use the course split helpers, validate the
fixed contracts, and write Parquet. It must not re-read Bronze or Silver.
"""

import hashlib
import os
import pandas as pd
from quantum_lake_student.config import Settings
from quantum_lake_student.connections import postgres_connection
from quantum_lake_student.ml import google_data_split
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

def run(run_id: str) -> StageResult:
    # raise NotImplementedError("Implement analysis outputs and ML input tables")
    result = StageResult(stage="build_ml_tables", run_id=run_id)
    settings = Settings.from_environment()

    os.makedirs("results/part2/ml", exist_ok=True)

    df_google_ml = get_google_MlTables(settings)
    df_google_ml.to_parquet("results/part2/ml/ml_google_decoder_example.parquet", index=False)

    result.input_count = len(df_google_ml)
    result.output_count = len(df_google_ml)
    result.issue_count = 0
    result.finish()
    return result
