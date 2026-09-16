"""Parse, check, and connect the supplied QEC data.

Apply documented checks, normalize nested/wide data, connect valid
relationships, and write prepared Parquet tables with chosen column names and
types. Write invalid records and the reason for exclusion to a machine-readable
data-issues output. The project README defines where generated files live.
"""

import hashlib
import io
import os
import zipfile
import pandas as pd
import yaml

from quantum_lake_student.models import StageResult
from quantum_lake_student.connections import minio_client 
from quantum_lake_student.config import Settings # connection details for pipeline
from quantum_lake_student.formats import iter_b8_records, parse_01_records


class Utility:

    def __init__(self):
        super()

    @staticmethod
    def say_hello(name: str = "world") -> str:
        return f"Hello, {name}!"

    @staticmethod
    def fetch_raw_data_bytes(settings) -> bytes:
        client = minio_client(settings)  # connection
        key = "bronze/source=google_qec/google-surface-code-curated.zip"
        response = client.get_object(settings.s3_bucket, key)  # retrieve the dataset
        raw_bytes = response.read()  # load all the data into RAM
        response.close()
        response.release_conn()  # return the connection
        return raw_bytes

    @staticmethod
    def list_zip_folders(raw_bytes: bytes) -> list[str]:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            names = z.namelist()
        return sorted({n.split("/")[0] for n in names if "/" in n and not n.startswith("__")}) # set of folder name (gets all unique values!)


    @staticmethod
    def read_zip_member_bytes(raw_bytes: bytes, member_name: str) -> bytes:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            if member_name in z.namelist():
                return z.read(member_name)
        return b""

    @staticmethod
    def read_zip_member_yaml(raw_bytes: bytes, member_name: str) -> dict:
        raw = Utility.read_zip_member_bytes(raw_bytes, member_name)
        return yaml.safe_load(raw) if raw else {}

    @staticmethod
    def read_b8_records(
        raw_bytes: bytes, member_name: str, bits_per_record: int
    ) -> list[tuple[int, ...]]:
        raw = Utility.read_zip_member_bytes(raw_bytes, member_name)
        if not raw or bits_per_record == 0:
            return []
        return list(iter_b8_records(raw, bits_per_record=bits_per_record))

    @staticmethod
    def read_01_records(raw_bytes: bytes, member_name: str) -> list[int]:
        raw = Utility.read_zip_member_bytes(raw_bytes, member_name)
        if not raw:
            return []
        return parse_01_records(raw)

    @staticmethod
    def check_equal_lengths(**named_lists) -> bool:

        active_lists = {
            name: lst for name, lst in named_lists.items() if len(lst) > 0
        } # we removed empty lists
        lengths = {name: len(lst) for name, lst in active_lists.items()}
        unique_lengths = set(lengths.values())
        if len(unique_lengths) == 1:
            print(
                f"OK: all {len(active_lists)} active lists have length"
                f" {unique_lengths.pop()}"
            )
            return True
        print("MISMATCH in lengths:")
        for name, length in lengths.items():
            print(f"  {name}: {length}")
        return False

    @staticmethod
    def count_set_bits(bit_tuple: tuple[int, ...]) -> int:
        return sum(bit_tuple)

    @staticmethod
    def pack_bits_to_bytes(bit_tuple: tuple[int, ...]) -> bytes:
        num_bytes = (len(bit_tuple) + 7) // 8
        out = bytearray(num_bytes)
        for bit_index, bit in enumerate(bit_tuple):
            if bit:
                out[bit_index // 8] |= 1 << (bit_index % 8)
        return bytes(out)


    @staticmethod
    def build_shot_rows(raw_bytes: bytes, folder: str) -> list[dict]:
        properties = Utility.read_zip_member_yaml(
            raw_bytes, f"{folder}/properties.yml"
        )

        measurements = Utility.read_b8_records(
            raw_bytes,
            f"{folder}/measurements.b8",
            properties.get("circuit_measurements", 0),
        )
        detections = Utility.read_b8_records(
            raw_bytes,
            f"{folder}/detection_events.b8",
            properties.get("circuit_detectors", 0),
        )
        sweeps = Utility.read_b8_records(
            raw_bytes,
            f"{folder}/sweep.b8",
            properties.get("circuit_sweep_bits", 0),
        )

        actual_flips = Utility.read_01_records(
            raw_bytes, f"{folder}/obs_flips_actual.01"
        )
        belief = Utility.read_01_records(
            raw_bytes,
            f"{folder}/obs_flips_predicted_by_belief_matching.01",
        )
        correlated = Utility.read_01_records(
            raw_bytes,
            f"{folder}/obs_flips_predicted_by_correlated_matching.01",
        )
        pymatching = Utility.read_01_records(
            raw_bytes, f"{folder}/obs_flips_predicted_by_pymatching.01"
        )
        tensor_net = Utility.read_01_records(
            raw_bytes,
            f"{folder}/obs_flips_predicted_by_tensor_network_contraction.01",
        )

        ok = Utility.check_equal_lengths(
            measurements=measurements,
            detections=detections,
            sweeps=sweeps,
            actual_flips=actual_flips,
            belief=belief,
            correlated=correlated,
            pymatching=pymatching,
            tensor_net=tensor_net,
        )
        if not ok:
            raise ValueError(f"Companion file length mismatch in {folder}")

        exp_id = f"google_{folder}"

        archive = hashlib.sha256(raw_bytes).hexdigest()  # proof of the version


        trace_rows = []  # storage for trace table
        shot_rows = []  # storage for shot rows
        for shot_index in range(len(actual_flips)):
            shot_rec_id = f"google_shot_{hashlib.md5(f'{exp_id}_{shot_index}'.encode()).hexdigest()[:12]}"
            shot_rows.append(
                {
                    "source_record_id": shot_rec_id,
                    "experiment_id": exp_id,
                    "shot_index": shot_index,
                    "measurement_bits": Utility.pack_bits_to_bytes(
                        measurements[shot_index]
                    ),
                    "sweep_bits": (
                        Utility.pack_bits_to_bytes(sweeps[shot_index])
                        if sweeps
                        else b""
                    ),
                    "detector_bits": Utility.pack_bits_to_bytes(
                        detections[shot_index]
                    ),
                    "detector_event_count": Utility.count_set_bits(
                        detections[shot_index]
                    ),
                    "actual_observable_flip": bool(actual_flips[shot_index]),
                    "belief_matching_prediction": (
                        bool(belief[shot_index]) if belief else False
                    ),
                    "correlated_matching_prediction": (
                        bool(correlated[shot_index]) if correlated else False
                    ),
                    "pymatching_prediction": (
                        bool(pymatching[shot_index]) if pymatching else False
                    ),
                    "tensor_network_contraction_prediction": (
                        bool(tensor_net[shot_index]) if tensor_net else False
                    ),
                }
            )

            trace_rows.append(
                {
                    "source_record_id": shot_rec_id,
                    "source_name": "google_qec",
                    "bronze_object": "bronze/source=google_qec/google-surface-code-curated.zip",
                    "archive_member": folder,
                    "record_locator": "experiment_metadata",
                    "input_sha256": archive,
                }
            )
        return shot_rows, trace_rows

    @staticmethod
    def build_experiment_rows(raw_bytes: bytes, folder: str) -> list[dict]:
        properties = Utility.read_zip_member_yaml(
            raw_bytes, f"{folder}/properties.yml"
        )
        exp_rows = []  # storage for experiment rows
        trace_rows = []  # storage for trace table
        exp_id = f"google_{folder}"
        exp_rec_id = f"exp_{hashlib.md5(exp_id.encode()).hexdigest()[:12]}"
        archive = hashlib.sha256(raw_bytes).hexdigest() # proof of the version
        exp_rows.append(
            {
                "source_record_id": exp_rec_id,
                "experiment_id": exp_id,
                "basis": properties.get("basis",0),
                "distance": properties.get("distance",0),
                "rounds": properties.get("rounds",0),
                "shots": properties.get("shots",0),
                "center_row": properties.get("center_data_qubit_row",0),
                "center_col": properties.get("center_data_qubit_col",0),
                "measurement_count": properties.get("circuit_measurements",0),
                "detector_count": properties.get("circuit_detectors",0)
            }
        )
        trace_rows.append(
            {
                "source_record_id": exp_rec_id,
                "source_name": "google_qec",
                "bronze_object": "bronze/source=google_qec/google-surface-code-curated.zip",
                "archive_member": folder,
                "record_locator": "experiment_metadata",
                "input_sha256": archive,
            }
        )
        return exp_rows, trace_rows

    @staticmethod
    def google_experiment_table(raw_bytes: bytes):
        folders = Utility.list_zip_folders(raw_bytes)
        all_experiment_rows = []
        all_trace_rows = []
        for folder in folders:
            exp_rows, trace_rows = Utility.build_experiment_rows(raw_bytes, folder)
            all_experiment_rows.extend(exp_rows)
            all_trace_rows.extend(trace_rows)

        experiment_df = pd.DataFrame(all_experiment_rows).astype(
            {
                "distance": "int32",
                "rounds": "int32",
                "shots": "int64",
                "center_row": "int32",
                "center_col": "int32",
                "measurement_count": "int32",
                "detector_count": "int32"
            }
        )
        
        trace_df = pd.DataFrame(all_trace_rows)
        return experiment_df, trace_df, len(folders)


    @staticmethod
    def google_shots_table(raw_bytes: bytes):
        folders = Utility.list_zip_folders(raw_bytes)
        all_shot_rows = []
        all_trace_rows = []
        for folder in folders:
            shot_rows, trace_rows = Utility.build_shot_rows(raw_bytes, folder)
            all_shot_rows.extend(shot_rows)
            all_trace_rows.extend(trace_rows)

        shot_df = pd.DataFrame(all_shot_rows).astype(
            {
                "source_record_id": "string",
                "experiment_id": "string",
                "shot_index": "int64",
                "measurement_bits": "object",
                "sweep_bits": "object",
                "detector_bits": "object",
                "detector_event_count": "int32",
                "actual_observable_flip": "bool",
                "belief_matching_prediction": "bool",
                "correlated_matching_prediction": "bool",
                "pymatching_prediction": "bool",
                "tensor_network_contraction_prediction": "bool",
            }
        )
        trace_df = pd.DataFrame(all_trace_rows)
        
        return shot_df, trace_df, len(all_shot_rows)



def run(run_id: str) -> StageResult:
    result = StageResult(stage="prepare_data", run_id=run_id)

    settings = Settings.from_environment()
    os.makedirs("silver/google_qec", exist_ok = True)
    os.makedirs("results/part1", exist_ok = True)

    raw_bytes = Utility.fetch_raw_data_bytes(settings)

    df_exp, df_trace1, exp_dirs = Utility.google_experiment_table(raw_bytes)
    df_shot, df_trace2, shot_count = Utility.google_shots_table(raw_bytes)

    df_trace = pd.concat([df_trace1, df_trace2], axis=0, ignore_index=True)

    df_exp.to_parquet("silver/google_qec/experiment.parquet")
    df_shot.to_parquet("silver/google_qec/shots.parquet")
    df_trace.to_parquet("results/part1/source_trace.parquet")

    result.input_count = exp_dirs + shot_count
    result.output_count = len(df_exp) + len(df_shot)
    result.issue_count = (exp_dirs + shot_count) - (len(df_exp) + len(df_exp))

    # client = minio_client(settings)
    # upload_map = {
    #     local_exp_path: "silver/google_qec/experiment.parquet",
    #     local_shot_path: "silver/google_qec/shot.parquet",
    #     local_trace_path: "results/part1/source_trace.parquet",
    # }
    #
    # for local_file, s3_key in upload_map.items():
    #     client.fput_object(
    #         bucket_name=settings.s3_bucket,
    #         object_name=s3_key,
    #         file_path=local_file
    #     )
    #

    result.finish()



    return result
