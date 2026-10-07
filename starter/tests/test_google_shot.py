import io
import zipfile

import pytest
import yaml

from quantum_lake_student.pipeline_utils import PipelineError
from quantum_lake_student.stages.prepare_data import GoogleQecUtility

FOLDER = "surface_code_bX_d3_r25_center_3_5"
EXP_ID = f"google_{FOLDER}"

PROPS = {
    "basis": "X",
    "distance": 3,
    "rounds": 25,
    "shots": 2,
    "center_data_qubit_row": 3,
    "center_data_qubit_col": 5,
    "circuit_measurements": 9,
    "circuit_sweep_bits": 0,
    "circuit_detectors": 8,
}


def make_files():
    return {
        "properties.yml": yaml.safe_dump(PROPS).encode(),
        "measurements.b8": bytes([1, 0, 255, 1]),
        "detection_events.b8": bytes([3, 0]),
        "obs_flips_actual.01": b"1\n0\n",
        "obs_flips_predicted_by_belief_matching.01": b"1\n0\n",
        "obs_flips_predicted_by_correlated_matching.01": b"0\n0\n",
        "obs_flips_predicted_by_pymatching.01": b"1\n1\n",
        "obs_flips_predicted_by_tensor_network_contraction.01": b"1\n0\n",
    }


def make_archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in files.items():
            z.writestr(f"{FOLDER}/{name}", data)
    return buffer.getvalue()


def test_valid_shots_are_published():
    raw = make_archive(make_files())
    shots, trace, issues, shots_read = GoogleQecUtility.google_shots_table(raw, {EXP_ID})
    assert len(shots) == 2
    assert len(trace) == 14
    assert trace["archive_member"].nunique() == 7
    assert len(issues) == 0
    assert shots_read == 2
    assert shots["detector_event_count"].tolist() == [2, 0]
    assert shots["measurement_bits"][1] == bytes([255, 1])


def test_nonzero_padding_rejects_only_that_shot():
    files = make_files()
    files["measurements.b8"] = bytes([1, 0, 255, 3])
    raw = make_archive(files)
    shots, trace, issues, shots_read = GoogleQecUtility.google_shots_table(raw, {EXP_ID})
    assert len(shots) == 1
    assert len(issues) == 1
    assert issues["rule_id"][0] == "google_shot_b8_padding_zero"
    assert issues["source_record_id"][0] is not None


def test_length_mismatch_rejects_experiment_shots():
    files = make_files()
    files["obs_flips_predicted_by_pymatching.01"] = b"1\n"
    raw = make_archive(files)
    shots, trace, issues, shots_read = GoogleQecUtility.google_shots_table(raw, {EXP_ID})
    assert len(shots) == 0
    assert issues["rule_id"].tolist() == ["google_shot_companion_lengths"]


def test_bad_01_value_is_recorded():
    files = make_files()
    files["obs_flips_actual.01"] = b"1\n2\n"
    raw = make_archive(files)
    shots, trace, issues, shots_read = GoogleQecUtility.google_shots_table(raw, {EXP_ID})
    assert len(shots) == 0
    assert issues["rule_id"].tolist() == ["google_shot_file_format"]


def test_shots_of_rejected_experiment_are_skipped():
    raw = make_archive(make_files())
    shots, trace, issues, shots_read = GoogleQecUtility.google_shots_table(raw, set())
    assert len(shots) == 0
    assert issues["rule_id"].tolist() == ["google_shot_experiment_accepted"]


def test_missing_companion_file_stops_the_run():
    files = make_files()
    del files["obs_flips_actual.01"]
    raw = make_archive(files)
    with pytest.raises(PipelineError):
        GoogleQecUtility.google_shots_table(raw, {EXP_ID})
