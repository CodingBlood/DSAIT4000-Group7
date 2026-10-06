"""Relational integration stage.

Create the student-designed PostgreSQL tables and load them as one all-or-
nothing update. Primary/foreign keys, value checks, and indexes are part of the
deliverable. Repeated loads must not create duplicate records.
"""

import os
import re
import pandas as pd
import json
import hashlib
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
    
CREATE TABLE IF NOT EXISTS syndrome_experiment (
    experiment_id      TEXT PRIMARY KEY,
    distance             INT NOT NULL CHECK (distance > 0),
    physical_fault_rate    DOUBLE PRECISION NOT NULL CHECK (physical_fault_rate >= 0)
);

CREATE TABLE IF NOT EXISTS syndrome_pattern (
    pattern_id      TEXT PRIMARY KEY,
    syndrome_bits     BYTEA NOT NULL UNIQUE,
    round_count         INT NOT NULL CHECK (round_count = 4),
    check_count           INT NOT NULL CHECK (check_count = 4)
);

CREATE TABLE IF NOT EXISTS syndrome_observation (
    source_record_id     TEXT PRIMARY KEY,
    experiment_id           TEXT NOT NULL REFERENCES syndrome_experiment(experiment_id),
    pattern_id                 TEXT NOT NULL REFERENCES syndrome_pattern(pattern_id),
    logical_error_label           BOOLEAN NOT NULL,
    quantity                        BIGINT NOT NULL CHECK (quantity > 0)
);

CREATE INDEX IF NOT EXISTS idx_syn_obs_experiment ON syndrome_observation(experiment_id);
CREATE INDEX IF NOT EXISTS idx_syn_obs_pattern ON syndrome_observation(pattern_id);

--Sahil's Code.
CREATE TABLE IF NOT EXISTS circuit (
    circuit_id            TEXT PRIMARY KEY,
    source_record_id      TEXT NOT NULL UNIQUE,
    benchmark_name        TEXT NOT NULL,
    variant               TEXT NOT NULL CHECK (variant IN ('source', 'transpiled')),
    qubit_count           INT NOT NULL CHECK (qubit_count > 0),
    measurement_count     INT NOT NULL CHECK (measurement_count >= 0),
    two_qubit_gate_count  INT NOT NULL CHECK (two_qubit_gate_count >= 0),
    UNIQUE (benchmark_name, variant)
);
 
CREATE TABLE IF NOT EXISTS circuit_register (
    circuit_id      TEXT NOT NULL REFERENCES circuit(circuit_id),
    register_order  INT NOT NULL,
    register_name   TEXT NOT NULL,
    register_type   TEXT NOT NULL CHECK (register_type IN ('qreg', 'creg')),
    register_size   INT NOT NULL CHECK (register_size > 0),
    PRIMARY KEY (circuit_id, register_order),
    UNIQUE (circuit_id, register_name)
);
 
CREATE TABLE IF NOT EXISTS circuit_qubit (
    circuit_id     TEXT NOT NULL,
    qubit_name     TEXT NOT NULL,
    register_name  TEXT NOT NULL,
    qubit_index    INT NOT NULL CHECK (qubit_index >= 0),
    PRIMARY KEY (circuit_id, qubit_name),
    FOREIGN KEY (circuit_id, register_name)
        REFERENCES circuit_register (circuit_id, register_name)
);
 
CREATE TABLE IF NOT EXISTS stabilizer_check (
    circuit_id         TEXT NOT NULL REFERENCES circuit(circuit_id),
    check_id           TEXT NOT NULL,
    source_record_id   TEXT NOT NULL UNIQUE,
    ancilla_qubit      TEXT NOT NULL,
    syndrome_register  TEXT NOT NULL,
    syndrome_index     INT NOT NULL CHECK (syndrome_index >= 0),
    PRIMARY KEY (circuit_id, check_id),
    FOREIGN KEY (circuit_id, ancilla_qubit)
        REFERENCES circuit_qubit (circuit_id, qubit_name),
    FOREIGN KEY (circuit_id, syndrome_register)
        REFERENCES circuit_register (circuit_id, register_name)
);
 
CREATE TABLE IF NOT EXISTS stabilizer_check_data_qubit (
    circuit_id  TEXT NOT NULL,
    check_id    TEXT NOT NULL,
    data_qubit  TEXT NOT NULL,
    PRIMARY KEY (circuit_id, check_id, data_qubit),
    FOREIGN KEY (circuit_id, check_id)
        REFERENCES stabilizer_check (circuit_id, check_id),
    FOREIGN KEY (circuit_id, data_qubit)
        REFERENCES circuit_qubit (circuit_id, qubit_name)
);
 
CREATE TABLE IF NOT EXISTS conditional_correction (
    source_record_id    TEXT PRIMARY KEY,
    circuit_id          TEXT NOT NULL REFERENCES circuit(circuit_id),
    condition_register  TEXT NOT NULL,
    condition_value     BIGINT NOT NULL CHECK (condition_value >= 0),
    gate                TEXT NOT NULL,
    target_qubit        TEXT NOT NULL,
    FOREIGN KEY (circuit_id, condition_register)
        REFERENCES circuit_register (circuit_id, register_name),
    FOREIGN KEY (circuit_id, target_qubit)
        REFERENCES circuit_qubit (circuit_id, qubit_name)
);
 
CREATE INDEX IF NOT EXISTS idx_qubit_circuit ON circuit_qubit(circuit_id);
CREATE INDEX IF NOT EXISTS idx_check_circuit ON stabilizer_check(circuit_id);
CREATE INDEX IF NOT EXISTS idx_corr_circuit ON conditional_correction(circuit_id);
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

def load_syndromes(cur, df_syn: pd.DataFrame) -> None:
    experiments = df_syn[["experiment_id", "physical_fault_rate"]].drop_duplicates("experiment_id")
    experiments["distance"] = experiments["experiment_id"].str.extract(r"_d(\d+)_").astype(int)
    cur.executemany(
        "INSERT INTO syndrome_experiment (experiment_id, distance, physical_fault_rate) VALUES (%s, %s, %s)",
        list(experiments[["experiment_id", "distance", "physical_fault_rate"]].itertuples(index=False, name=None)),
    )

    df_syn = df_syn.copy()
    df_syn["pattern_id"] = df_syn["syndrome_bits"].apply(lambda b: f"pat_{hashlib.md5(b).hexdigest()[:12]}")
    patterns = df_syn[["pattern_id", "syndrome_bits", "round_count", "check_count"]].drop_duplicates("pattern_id")
    cur.executemany(
        "INSERT INTO syndrome_pattern (pattern_id, syndrome_bits, round_count, check_count) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (pattern_id) DO NOTHING",
        [(r.pattern_id, bytes(r.syndrome_bits), int(r.round_count), int(r.check_count)) for r in patterns.itertuples()],
    )

    cur.executemany(
        "INSERT INTO syndrome_observation (source_record_id, experiment_id, pattern_id, logical_error_label, quantity) "
        "VALUES (%s, %s, %s, %s, %s)",
        [(r.source_record_id, r.experiment_id, r.pattern_id, bool(r.logical_error_label), int(r.quantity))
         for r in df_syn.itertuples()],
    )
def load_qasm(cur, df_circuit: pd.DataFrame, df_check: pd.DataFrame,
              df_corr: pd.DataFrame) -> None:
    reg_pattern = re.compile(r"(qreg|creg)\s+(\w+)\[(\d+)\]")
    bit_pattern = re.compile(r"(\w+)\[(\d+)\]")
 
    # circuit, circuit_register, circuit_qubit
    circuit_rows, register_rows, qubit_rows = [], [], []
    for r in df_circuit.itertuples():
        circuit_rows.append((
            r.circuit_id, r.source_record_id, r.benchmark_name, r.variant,
            int(r.qubit_count), int(r.measurement_count), int(r.two_qubit_gate_count),
        ))
        for order, (kind, name, size) in enumerate(reg_pattern.findall(r.register_declarations)):
            register_rows.append((r.circuit_id, order, name, kind, int(size)))
            if kind == "qreg":
                for i in range(int(size)):
                    qubit_rows.append((r.circuit_id, f"{name}[{i}]", name, i))
 
    cur.executemany(
        "INSERT INTO circuit (circuit_id, source_record_id, benchmark_name, variant, "
        "qubit_count, measurement_count, two_qubit_gate_count) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        circuit_rows,
    )
    cur.executemany(
        "INSERT INTO circuit_register (circuit_id, register_order, register_name, "
        "register_type, register_size) VALUES (%s,%s,%s,%s,%s)",
        register_rows,
    )
    cur.executemany(
        "INSERT INTO circuit_qubit (circuit_id, qubit_name, register_name, qubit_index) "
        "VALUES (%s,%s,%s,%s)",
        qubit_rows,
    )
 
    # stabilizer_check and its data-qubit junction table
    check_rows, data_rows = [], []
    for r in df_check.itertuples():
        match = bit_pattern.fullmatch(r.syndrome_bit)
        if match is None:
            raise ValueError(f"Unexpected syndrome_bit format: {r.syndrome_bit!r}")
        check_rows.append((
            r.circuit_id, r.check_id, r.source_record_id, r.ancilla_qubit,
            match.group(1), int(match.group(2)),
        ))
        for data_qubit in list(r.data_qubits):
            data_rows.append((r.circuit_id, r.check_id, str(data_qubit)))
 
    cur.executemany(
        "INSERT INTO stabilizer_check (circuit_id, check_id, source_record_id, "
        "ancilla_qubit, syndrome_register, syndrome_index) VALUES (%s,%s,%s,%s,%s,%s)",
        check_rows,
    )
    cur.executemany(
        "INSERT INTO stabilizer_check_data_qubit (circuit_id, check_id, data_qubit) "
        "VALUES (%s,%s,%s)",
        data_rows,
    )
 
    # conditional_correction
    cur.executemany(
        "INSERT INTO conditional_correction (source_record_id, circuit_id, "
        "condition_register, condition_value, gate, target_qubit) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        [
            (r.source_record_id, r.circuit_id, r.condition_register,
             int(r.condition_value), r.gate, r.target_qubit)
            for r in df_corr.itertuples()
        ],
    )
def run(run_id: str) -> StageResult:
    result = StageResult(stage="load_postgres", run_id=run_id)

    settings = Settings.from_environment()

    exp_path = "silver/google_qec/experiment.parquet"
    shot_path = "silver/google_qec/shot.parquet"
    syndrome_path = "silver/qec_syndromes/syndrome_observation.parquet"

    circuit_path = "silver/qasmbench/circuit.parquet"
    check_path = "silver/qasmbench/stabilizer_check.parquet"
    correction_path = "silver/qasmbench/conditional_correction.parquet"
    if not os.path.exists(exp_path):
        raise FileNotFoundError(
            f"{exp_path} not found. Run the prepare_data stage first."
        )
    if not os.path.exists(shot_path):
        raise FileNotFoundError(
            f"{shot_path} not found. Run the prepare_data stage first."
        )
    if not os.path.exists(syndrome_path):
        raise FileNotFoundError(
            f"{syndrome_path} not found. Run the prepare_data stage first."
        )
    for path in (circuit_path, check_path, correction_path):
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found. Run the prepare_data stage first."
            )

    df_exp = pd.read_parquet(exp_path)
    df_shots = pd.read_parquet(shot_path)
    df_syndromes = pd.read_parquet(syndrome_path)

    df_circuit = pd.read_parquet(circuit_path)
    df_check = pd.read_parquet(check_path)
    df_corr = pd.read_parquet(correction_path)

    total_input = (len(df_exp) + len(df_shots) + len(df_syndromes)
                   + len(df_circuit) + len(df_check) + len(df_corr))

    with postgres_connection(settings) as connection:
        with connection.transaction():
            with connection.cursor() as cur:
                cur.execute(DDL)

                cur.execute(
                "TRUNCATE TABLE conditional_correction, stabilizer_check_data_qubit, "
                    "stabilizer_check, circuit_qubit, circuit_register, circuit, "
                    "shot_prediction, shot, decoder, experiment, "
                    "syndrome_experiment, syndrome_observation, syndrome_pattern "
                    "RESTART IDENTITY CASCADE"
                )

                load_experiment(cur, df_exp)
                load_decoders(cur)
                load_syndromes(cur, df_syndromes)
                load_qasm(cur, df_circuit, df_check, df_corr)

                if df_shots is not None:
                    load_shots(cur, df_shots)

                cur.execute("SELECT COUNT(*) FROM experiment")
                exp_count = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM shot")
                shot_count = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM syndrome_observation")
                syndrome_count = cur.fetchone()[0]

                cur.execute(
                    "SELECT (SELECT COUNT(*) FROM circuit) + "
                    "(SELECT COUNT(*) FROM stabilizer_check) + "
                    "(SELECT COUNT(*) FROM conditional_correction)"
                )
                qasm_count = cur.fetchone()[0]

    result.input_count = total_input
    result.output_count = exp_count + shot_count + syndrome_count + qasm_count
    result.issue_count = result.input_count - result.output_count

    row_counts_path = "results/part1/row_counts.json"
    existing = {}
    if os.path.exists(row_counts_path):
        with open(row_counts_path) as f:
            existing = json.load(f)

    existing["gold_load"] = {
        "read": result.input_count,
        "accepted": result.output_count,
        "rejected": result.issue_count,
    }

    with open(row_counts_path, "w") as f:
        json.dump(existing, f, indent=2)

    result.finish()
    return result