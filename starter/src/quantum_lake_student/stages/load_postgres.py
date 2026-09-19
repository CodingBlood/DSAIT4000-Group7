"""Relational integration stage.

Create the student-designed PostgreSQL tables and load them as one all-or-
nothing update. Primary/foreign keys, value checks, and indexes are part of the
deliverable. Repeated loads must not create duplicate records.
"""

import os
import pandas as pd

from quantum_lake_student.config import Settings
from quantum_lake_student.connections import postgres_connection
from quantum_lake_student.models import StageResult

DECODER_COLUMNS = {
    "belief_matching_prediction": "belief_matching",
    "correlated_matching_prediction": "correlated_matching",
    "pymatching_prediction": "pymatching",
    "tensor_network_contraction_prediction": "tensor_network_contraction",
}

DDL = """
CREATE TABLE IF NOT EXISTS experiment (
    experiment_id       TEXT PRIMARY KEY,
    source_record_id    TEXT NOT NULL UNIQUE,
    basis                TEXT NOT NULL CHECK (basis IN ('X', 'Z')),
    distance              INT NOT NULL CHECK (distance > 0),
    rounds                INT NOT NULL CHECK (rounds > 0),
    shots                 BIGINT NOT NULL CHECK (shots > 0),
    center_row            INT NOT NULL,
    center_col            INT NOT NULL,
    measurement_count     INT NOT NULL,
    detector_count        INT NOT NULL
);

CREATE TABLE IF NOT EXISTS shot (
    shot_id                 TEXT PRIMARY KEY,
    source_record_id        TEXT NOT NULL UNIQUE,
    experiment_id            TEXT NOT NULL REFERENCES experiment(experiment_id),
    shot_index                BIGINT NOT NULL,
    measurement_bits          BYTEA NOT NULL,
    sweep_bits                 BYTEA NOT NULL,
    detector_bits               BYTEA NOT NULL,
    detector_event_count         INT NOT NULL,
    actual_observable_flip        BOOLEAN NOT NULL,
    UNIQUE (experiment_id, shot_index)
);

CREATE TABLE IF NOT EXISTS decoder (
    decoder_id      TEXT PRIMARY KEY,
    decoder_name    TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS shot_prediction (
    shot_id          TEXT NOT NULL REFERENCES shot(shot_id),
    decoder_id       TEXT NOT NULL REFERENCES decoder(decoder_id),
    predicted_flip   BOOLEAN NOT NULL,
    PRIMARY KEY (shot_id, decoder_id)
);

CREATE INDEX IF NOT EXISTS idx_shot_experiment_id ON shot(experiment_id);
CREATE INDEX IF NOT EXISTS idx_shot_prediction_decoder_id ON shot_prediction(decoder_id);
"""

def load_experiment(cur, df_exp: pd.DataFrame) -> int:
    cur.executemany(
        """
        INSERT INTO experiment (
            experiment_id, source_record_id, basis, distance, rounds, shots,
            center_row, center_col, measurement_count, detector_count
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                row.experiment_id,
                row.source_record_id,
                row.basis,
                int(row.distance),
                int(row.rounds),
                int(row.shots),
                int(row.center_row),
                int(row.center_col),
                int(row.measurement_count),
                int(row.detector_count),
            )
            for row in df_exp.itertuples()
        ],
    )
    return len(df_exp)

def load_decoders(cur) -> int:
    cur.executemany(
        "INSERT INTO decoder (decoder_id, decoder_name) VALUES (%s, %s) "
        "ON CONFLICT (decoder_id) DO NOTHING",
        [(decoder_id, decoder_id) for decoder_id in DECODER_COLUMNS.values()],
    )
    return len(DECODER_COLUMNS)

def load_shots(cur, df_shots: pd.DataFrame) -> tuple[int, int]:
    cur.executemany(
        """
        INSERT INTO shot (
            shot_id, source_record_id, experiment_id, shot_index,
            measurement_bits, sweep_bits, detector_bits,
            detector_event_count, actual_observable_flip
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                row.source_record_id,
                row.source_record_id,
                row.experiment_id,
                int(row.shot_index),
                bytes(row.measurement_bits),
                bytes(row.sweep_bits),
                bytes(row.detector_bits),
                int(row.detector_event_count),
                bool(row.actual_observable_flip),
            )
            for row in df_shots.itertuples()
        ],
    )

    prediction_rows = []
    for row in df_shots.itertuples():
        for column, decoder_id in DECODER_COLUMNS.items():
            prediction_rows.append(
                (row.source_record_id, decoder_id, bool(getattr(row, column)))
            )

    cur.executemany(
        """
        INSERT INTO shot_prediction (shot_id, decoder_id, predicted_flip)
        VALUES (%s, %s, %s)
        """,
        prediction_rows,
    )

    return len(df_shots), len(prediction_rows)

def run(run_id: str) -> StageResult:
    result = StageResult(stage="load_postgres", run_id=run_id)

    settings = Settings.from_environment()

    exp_path = "silver/google_qec/experiment.parquet"
    shot_path = "silver/google_qec/shots.parquet"

    if not os.path.exists(exp_path):
        raise FileNotFoundError(
            f"{exp_path} not found. Run the prepare_data stage first."
        )

    df_exp = pd.read_parquet(exp_path)
    df_shots = pd.read_parquet(shot_path) if os.path.exists(shot_path) else None

    total_input = len(df_exp) + (len(df_shots) if df_shots is not None else 0)

    with postgres_connection(settings) as connection:
        with connection.transaction():
            with connection.cursor() as cur:
                cur.execute(DDL)

                cur.execute(
                    "TRUNCATE TABLE shot_prediction, shot, decoder, experiment "
                    "RESTART IDENTITY CASCADE"
                )

                exp_count = load_experiment(cur, df_exp)
                decoder_count = load_decoders(cur)

                shot_count = 0
                prediction_count = 0
                if df_shots is not None:
                    shot_count, prediction_count = load_shots(cur, df_shots)

    result.input_count = total_input
    result.output_count = exp_count + shot_count + decoder_count + prediction_count
    result.issue_count = 0

    result.finish()
    return result