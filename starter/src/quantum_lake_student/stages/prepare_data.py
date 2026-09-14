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


def google_experiment_table(settings: Settings):

    client = minio_client(settings) # connection
    key = "bronze/source=google_qec/google-surface-code-curated.zip"
    response = client.get_object(settings.s3_bucket, key) # retrieve the dataset
    raw_bytes = response.read() # load all the data into RAM
    response.close()  
    response.release_conn() # return the connection

    archive = hashlib.sha256(raw_bytes).hexdigest() # proof of the version

    exp_rows = [] # storage for experiment table
    trace_rows = [] # storage for trace table

    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z: # wrap to treat like object
        all_members = z.namelist() # list every surface_code directory  
        exp_dirs = sorted(
            {m.split("/")[0] for m in all_members if "/" in m and not m.startswith("__")}
        )

        for exp_dir in exp_dirs:
            props = f"{exp_dir}/properties.yml" # the metadata
            if props not in all_members: # skip the directory with no metadata
                print("missing propeties, skip")
                continue
            props = yaml.safe_load(z.read(props)) # Python dictionary of metadata
            # no duplicates
            exp_id = f"google_{exp_dir}"
            exp_rec_id = f"exp_{hashlib.md5(exp_id.encode()).hexdigest()[:12]}"

            exp_rows.append(
                {
                    "source_record_id": exp_rec_id,
                    "experiment_id": exp_id,
                    "basis": props["basis"],
                    "distance": props["distance"],
                    "rounds": props["rounds"],
                    "shots": props["shots"],
                    "center_row": props["center_data_qubit_row"],
                    "center_col": props["center_data_qubit_col"],
                    "measurement_count": props["circuit_measurements"],
                    "detector_count": props["circuit_detectors"],
                }
            )
            trace_rows.append(
                {
                    "source_record_id": exp_rec_id,
                    "source_name": "google_qec",
                    "bronze_object": key,
                    "archive_member": exp_dir,
                    "record_locator": "experiment_metadata",
                    "input_sha256": archive,
                }
            )
    df_trace = pd.DataFrame(trace_rows)
    df_exp = pd.DataFrame(exp_rows).astype(
        {
            "distance" : "int32",
            "rounds" : "int32",
            "shots" : "int64",
            "center_row" : "int32",
            "center_col" : "int32",
            "measurement_count" : "int32",
            "detector_count" : "int32" 
        }
    )

    df_exp, df_trace
    return df_exp, df_trace, len(exp_dirs)
    


def run(run_id: str) -> StageResult:
    result = StageResult(stage="prepare_data", run_id=run_id)

    settings = Settings.from_environment()
    os.makedirs("silver/google_qec", exist_ok = True)
    os.makedirs("results/part1", exist_ok = True)

    df_exp, df_trace, exp_dirs = google_experiment_table(settings)

    df_exp.to_parquet("silver/google_qec/experiment.parquet")
    df_trace.to_parquet("results/part1/source_trace.parquet")

    result.input_count = exp_dirs
    result.output_count = len(df_exp) 
    result.issue_count = exp_dirs - len(df_exp) 

    result.finish()
    return result
