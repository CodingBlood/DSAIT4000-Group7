from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
import yaml

from quantum_lake_student.stages.prepare_data import GoogleQecUtility

EXP_DIR = "surface_code_bX_d3_r25_center_3_5"

VALID_PROPS = {
    "type": "surface_code_memory_experiment",
    "basis": "X",
    "rounds": 25,
    "distance": 3,
    "data_qubits": 9,
    "measure_qubits": 8,
    "shots": 50000,
    "center_data_qubit_row": 3,
    "center_data_qubit_col": 5,
    "circuit_measurements": 209,
    "circuit_sweep_bits": 9,
    "circuit_detectors": 200,
    "circuit_observables": 1,
    "circuit_qubits": 17,
}

# simulates a dir with properties.yml
def archive_of(props: dict | None, exp_dir:str = EXP_DIR) -> bytes:
    buffer = io.BytesIO() # in memory buffer
    with zipfile.ZipFile(buffer, "w") as z:
        if props is not None:
            # writes the properties to yaml file
            z.writestr(f"{exp_dir}/properties.yml", yaml.safe_dump(props))
        else:
            z.writestr(f"{exp_dir}/detection_events.b8", b"")
    return buffer.getvalue()

# test if we find unique top level directories 
def test_list_zip_folders():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("expA/properties.yml", "basis: X\n")
        z.writestr("expA/detection_events.b8", b"")
        z.writestr("expB/properties.yml", "basis: X\n")
        z.writestr("__MACOSX/expA/junk/", b"")

    folders = GoogleQecUtility.list_zip_folders(buffer.getvalue())
    assert folders == ["expA", "expB"]

# tests whether the experiment columns are valid values
def test_valid_experiment():
    raw = archive_of(VALID_PROPS)
    df_exp, df_trace, df_issues, total_dirs = GoogleQecUtility.google_experiment_table(raw)

    assert total_dirs == 1 # we have only one test folder
    assert len(df_exp) == 1
    assert len(df_issues) == 0

    row = df_exp.iloc[0]
    assert row["experiment_id"] == f"google_{EXP_DIR}"
    assert row["basis"] == "X"
    assert row["distance"] == 3
    assert row["rounds"] == 25
    assert row["shots"] == 50000
    assert row["center_row"] == 3
    assert row["center_col"] == 5
    assert row["measurement_count"] == 209
    assert row["detector_count"] == 200

def test_column_types():
    df_exp, _, _, _ = GoogleQecUtility.google_experiment_table(archive_of(VALID_PROPS))

    assert df_exp["distance"].dtype == "int32"
    assert df_exp["rounds"].dtype == "int32"
    assert df_exp["shots"].dtype == "int64"
    assert df_exp["center_row"].dtype == "int32"
    assert df_exp["center_col"].dtype == "int32"
    assert df_exp["measurement_count"].dtype == "int32"
    assert df_exp["detector_count"].dtype == "int32"

# see if values from shared tracing rule match with the google experiment values
def test_trace_row_back_to_bronze():
    df_exp, df_trace, _, _ = GoogleQecUtility.google_experiment_table(archive_of(VALID_PROPS))
    trace = df_trace.iloc[0]

    assert trace["source_record_id"] == df_exp.iloc[0]["source_record_id"]
    assert trace["source_name"] == "google_qec"
    assert trace["bronze_object"] == GoogleQecUtility.BRONZE_KEY
    assert trace["archive_member"] == EXP_DIR
    assert len(trace["input_sha256"]) == 64 

# assert whether the source_record_id matches from the build_experiment_rows function
def test_source_record_id():
    df_exp, _, _, _ = GoogleQecUtility.google_experiment_table(archive_of(VALID_PROPS))
    expected_id = f"exp_{hashlib.md5(f'google_{EXP_DIR}'.encode()).hexdigest()[:12]}"

    assert df_exp.iloc[0]["source_record_id"] == expected_id

# assert no issues appear
def test_no_issues_experiment():
    _, _, df_issues, _ = GoogleQecUtility.google_experiment_table(archive_of(VALID_PROPS))

    assert len(df_issues) == 0

# now test whether the broken dir has been processed as an issue
def test_missing_properties():
    df_exp, df_trace, df_issues, total_dirs = GoogleQecUtility.google_experiment_table(archive_of(None))

    assert total_dirs == 1
    assert len(df_exp) == 0
    assert len(df_trace) == 0
    assert len(df_issues) == 1

    issue = df_issues.iloc[0]
    assert issue["source_record_id"] is None
    assert issue["rule_id"] == "google_experiment_properties_present"
    assert issue["severity"] == "error"   
    assert issue["action"] == "rejected"  
    assert issue["reason"] == "properties missing or empty" 

# if you give incomplete properties, an issue should arise
def test_incomplete_properties():
    icomplete_properties = {"basis": "X", "rounds": 25}
    df_exp, df_trace, df_issues, total_dirs = GoogleQecUtility.google_experiment_table(archive_of(icomplete_properties))
    assert total_dirs == 1
    assert len(df_exp) == 0
    assert len(df_trace) == 0
    assert len(df_issues) == 1

    issue = df_issues.iloc[0]
    assert issue["rule_id"] == "google_experiment_properties_present"
    assert "distance" in issue["reason"]

# now lets see if the issues and valid experiment are getting logged correctly
def test_rejected_valid_in_one():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(f"{EXP_DIR}/properties.yml", yaml.safe_dump(VALID_PROPS))
        z.writestr("broken_exp/detection_events.b8", b"")

    df_exp, df_trace, df_issues, total_dirs = GoogleQecUtility.google_experiment_table(buffer.getvalue())

    # both broken and valid dir should be logged correctly
    assert total_dirs == 2
    assert len(df_exp) == 1
    assert len(df_issues) == 1

# if we parse the same dir twice, does anything change
def test_consequtive_parsing():
    raw = archive_of(VALID_PROPS)
    df_exp_a, df_trace_a, df_issues_a, n_a = GoogleQecUtility.google_experiment_table(raw)
    df_exp_b, df_trace_b, df_issues_b, n_b = GoogleQecUtility.google_experiment_table(raw)

    assert df_exp_a.equals(df_exp_b)
    assert df_trace_a.equals(df_trace_b)
    assert df_issues_a.equals(df_issues_b)
    assert n_a == n_b

def test_multiple_experiment_each_one_row():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        props_a = dict(VALID_PROPS)
        # change some data
        props_b = dict(VALID_PROPS, distance=5, center_data_qubit_row=5, center_data_qubit_col=5)
        z.writestr("surface_code_bX_d3_r25_center_3_5/properties.yml", yaml.safe_dump(props_a))
        z.writestr("surface_code_bX_d5_r25_center_5_5/properties.yml", yaml.safe_dump(props_b))

    df_exp, df_trace, df_issues, total_dirs =  GoogleQecUtility.google_experiment_table(buffer.getvalue())

    assert total_dirs == 2
    assert len(df_exp) == 2
    assert len(df_issues) == 0
    assert set(df_exp["distance"]) == {3,5}
    assert len(set(df_exp["source_record_id"])) == 2