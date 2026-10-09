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
import json
import ast
import re
from quantum_lake_student.models import StageResult
from quantum_lake_student.connections import minio_client 
from quantum_lake_student.config import Settings # connection details for pipeline
from quantum_lake_student.formats import parse_01_records
from quantum_lake_student.pipeline_utils import PipelineError, write_parquet

def fetch_bronze_bytes(settings, key: str) -> bytes:
    client = minio_client(settings)
    response = client.get_object(settings.s3_bucket, key)
    raw_bytes = response.read()
    response.close()
    response.release_conn()
    return raw_bytes

class GoogleQecUtility:
    BRONZE_KEY = "bronze/source=google_qec/google-surface-code-curated.zip"
    REQUIRED_SHOT_FILES = [
        "properties.yml",
        "measurements.b8",
        "detection_events.b8",
        "obs_flips_actual.01",
        "obs_flips_predicted_by_belief_matching.01",
        "obs_flips_predicted_by_correlated_matching.01",
        "obs_flips_predicted_by_pymatching.01",
        "obs_flips_predicted_by_tensor_network_contraction.01",
    ]
    TRACE_COLUMNS = ["source_record_id", "source_name", "bronze_object", "archive_member", "record_locator", "input_sha256"]
    SHOT_COLUMNS = [
        "source_record_id", "experiment_id", "shot_index", "measurement_bits", "sweep_bits",
        "detector_bits", "detector_event_count", "actual_observable_flip",
        "belief_matching_prediction", "correlated_matching_prediction",
        "pymatching_prediction", "tensor_network_contraction_prediction",
    ]

    def __init__(self):
        super()

    @staticmethod
    def say_hello(name: str = "world") -> str:
        return f"Hello, {name}!"


    @staticmethod
    def list_zip_folders(raw_bytes: bytes) -> list[str]:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            names = z.namelist()
        return sorted({n.split("/")[0] for n in names if
                       "/" in n and not n.startswith("__")})  # set of folder name (gets all unique values!)

    @staticmethod
    def read_zip_member_bytes(raw_bytes: bytes, member_name: str) -> bytes:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            if member_name in z.namelist():
                return z.read(member_name)
        return b""

    @staticmethod
    def read_zip_member_yaml(raw_bytes: bytes, member_name: str) -> dict:
        raw = GoogleQecUtility.read_zip_member_bytes(raw_bytes, member_name)
        return yaml.safe_load(raw) if raw else {}

    @staticmethod
    def read_b8_rows(raw_bytes: bytes, member_name: str, bit_count: int) -> list[bytes] | None:
        raw = GoogleQecUtility.read_zip_member_bytes(raw_bytes, member_name)
        if bit_count == 0:
            return []
        record_size = (bit_count + 7) // 8
        if len(raw) % record_size != 0:
            return None
        rows = []
        for start in range(0, len(raw), record_size):
            rows.append(raw[start:start + record_size])
        return rows

    @staticmethod
    def read_01_records(raw_bytes: bytes, member_name: str) -> list[int] | None:
        raw = GoogleQecUtility.read_zip_member_bytes(raw_bytes, member_name)
        try:
            return parse_01_records(raw)
        except ValueError:
            return None

    @staticmethod
    def padding_is_zero(record: bytes, bit_count: int) -> bool:
        used_bits = bit_count % 8
        if used_bits == 0 or len(record) == 0:
            return True
        return record[-1] >> used_bits == 0

    @staticmethod
    def count_set_bits_in_bytes(record: bytes) -> int:
        count = 0
        for value in record:
            count += value.bit_count()
        return count

    @staticmethod
    def shot_issue(exp_id: str, source_record_id, rule_id: str, observed_value: str, reason: str) -> dict:
        issue_key = f"{exp_id}:{source_record_id}:{rule_id}"
        return {
            "issue_id": "issue_" + hashlib.md5(issue_key.encode()).hexdigest()[:12],
            "source_record_id": source_record_id,
            "rule_id": rule_id,
            "severity": "error",
            "observed_value": observed_value,
            "action": "rejected",
            "reason": reason,
        }

    @staticmethod
    def build_shot_rows(raw_bytes: bytes, folder: str, accepted_experiment_ids: set):
        exp_id = f"google_{folder}"
        shot_rows = []
        trace_rows = []
        issue_rows = []

        if exp_id not in accepted_experiment_ids:
            issue_rows.append(GoogleQecUtility.shot_issue(
                exp_id, None, "google_shot_experiment_accepted", f"folder={folder}",
                "experiment was rejected, so its shots are not published",
            ))
            return shot_rows, trace_rows, issue_rows, 0

        properties = GoogleQecUtility.read_zip_member_yaml(raw_bytes, f"{folder}/properties.yml")
        declared_shots = int(properties["shots"])
        measurement_bit_count = int(properties["circuit_measurements"])
        detector_bit_count = int(properties["circuit_detectors"])
        sweep_bit_count = int(properties.get("circuit_sweep_bits", 0))

        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            members = set(z.namelist())
        required_files = list(GoogleQecUtility.REQUIRED_SHOT_FILES)
        if sweep_bit_count > 0:
            required_files.append("sweep.b8")
        for file_name in required_files:
            if f"{folder}/{file_name}" not in members:
                raise PipelineError(f"Required companion file {folder}/{file_name} is missing from the Google archive")

        measurements = GoogleQecUtility.read_b8_rows(raw_bytes, f"{folder}/measurements.b8", measurement_bit_count)
        detections = GoogleQecUtility.read_b8_rows(raw_bytes, f"{folder}/detection_events.b8", detector_bit_count)
        sweeps = GoogleQecUtility.read_b8_rows(raw_bytes, f"{folder}/sweep.b8", sweep_bit_count)
        actual_flips = GoogleQecUtility.read_01_records(raw_bytes, f"{folder}/obs_flips_actual.01")
        belief = GoogleQecUtility.read_01_records(raw_bytes, f"{folder}/obs_flips_predicted_by_belief_matching.01")
        correlated = GoogleQecUtility.read_01_records(raw_bytes, f"{folder}/obs_flips_predicted_by_correlated_matching.01")
        pymatching = GoogleQecUtility.read_01_records(raw_bytes, f"{folder}/obs_flips_predicted_by_pymatching.01")
        tensor_net = GoogleQecUtility.read_01_records(raw_bytes, f"{folder}/obs_flips_predicted_by_tensor_network_contraction.01")

        files = {
            "measurements.b8": measurements,
            "detection_events.b8": detections,
            "obs_flips_actual.01": actual_flips,
            "obs_flips_predicted_by_belief_matching.01": belief,
            "obs_flips_predicted_by_correlated_matching.01": correlated,
            "obs_flips_predicted_by_pymatching.01": pymatching,
            "obs_flips_predicted_by_tensor_network_contraction.01": tensor_net,
        }
        if sweep_bit_count > 0:
            files["sweep.b8"] = sweeps

        for file_name, records in files.items():
            if records is None:
                issue_rows.append(GoogleQecUtility.shot_issue(
                    exp_id, None, "google_shot_file_format", f"{folder}/{file_name}",
                    "b8 file is not a whole number of records, or 01 file has a value other than 0 or 1",
                ))
        if len(issue_rows) > 0:
            return shot_rows, trace_rows, issue_rows, declared_shots

        lengths_match = True
        for records in files.values():
            if len(records) != declared_shots:
                lengths_match = False
        if not lengths_match:
            observed = []
            for file_name, records in files.items():
                observed.append(f"{file_name}={len(records)}")
            issue_rows.append(GoogleQecUtility.shot_issue(
                exp_id, None, "google_shot_companion_lengths", f"shots={declared_shots}; " + ", ".join(observed),
                "companion files do not all contain the declared number of shots, so shots cannot be aligned",
            ))
            return shot_rows, trace_rows, issue_rows, declared_shots

        archive = hashlib.sha256(raw_bytes).hexdigest()
        accepted_ids = []
        accepted_locators = []
        for shot_index in range(declared_shots):
            shot_rec_id = f"google_shot_{hashlib.md5(f'{exp_id}_{shot_index}'.encode()).hexdigest()[:12]}"
            measurement_record = measurements[shot_index]
            detector_record = detections[shot_index]
            sweep_record = b""
            if sweep_bit_count > 0:
                sweep_record = sweeps[shot_index]

            bad_padding = []
            if not GoogleQecUtility.padding_is_zero(measurement_record, measurement_bit_count):
                bad_padding.append(f"measurements.b8 last byte={measurement_record[-1]}")
            if not GoogleQecUtility.padding_is_zero(detector_record, detector_bit_count):
                bad_padding.append(f"detection_events.b8 last byte={detector_record[-1]}")
            if not GoogleQecUtility.padding_is_zero(sweep_record, sweep_bit_count):
                bad_padding.append(f"sweep.b8 last byte={sweep_record[-1]}")
            if len(bad_padding) > 0:
                issue_rows.append(GoogleQecUtility.shot_issue(
                    exp_id, shot_rec_id, "google_shot_b8_padding_zero", "; ".join(bad_padding),
                    "unused padding bits after the last real bit must be zero",
                ))
                continue

            shot_rows.append(
                {
                    "source_record_id": shot_rec_id,
                    "experiment_id": exp_id,
                    "shot_index": shot_index,
                    "measurement_bits": measurement_record,
                    "sweep_bits": sweep_record,
                    "detector_bits": detector_record,
                    "detector_event_count": GoogleQecUtility.count_set_bits_in_bytes(detector_record),
                    "actual_observable_flip": bool(actual_flips[shot_index]),
                    "belief_matching_prediction": bool(belief[shot_index]),
                    "correlated_matching_prediction": bool(correlated[shot_index]),
                    "pymatching_prediction": bool(pymatching[shot_index]),
                    "tensor_network_contraction_prediction": bool(tensor_net[shot_index]),
                }
            )
            accepted_ids.append(shot_rec_id)
            accepted_locators.append(f"shot_index={shot_index}")

        for file_name in files:
            trace_rows.append(pd.DataFrame({
                "source_record_id": accepted_ids,
                "source_name": "google_qec",
                "bronze_object": GoogleQecUtility.BRONZE_KEY,
                "archive_member": f"{folder}/{file_name}",
                "record_locator": accepted_locators,
                "input_sha256": archive,
            }))
        return shot_rows, trace_rows, issue_rows, declared_shots

    @staticmethod
    def build_experiment_rows(raw_bytes: bytes, folder: str) -> list[dict]:
        properties = GoogleQecUtility.read_zip_member_yaml(
            raw_bytes, f"{folder}/properties.yml"
        )
        exp_rows = []  # storage for experiment rows
        trace_rows = []  # storage for trace table
        issue_rows = [] # for storage of missing/broken experiments
        exp_id = f"google_{folder}"
        exp_rec_id = f"exp_{hashlib.md5(exp_id.encode()).hexdigest()[:12]}"
        archive = hashlib.sha256(raw_bytes).hexdigest()  # proof of the version
        
        required_keys = [
            "basis", "distance", "rounds", "shots",
            "center_data_qubit_row", "center_data_qubit_col",
            "circuit_measurements", "circuit_detectors",
        ]

        # does the file have all the required data
        missing_keys = [key for key in required_keys if key not in properties]

        if not properties or missing_keys:
            reason = (
            "properties missing or empty"
            if not properties
            else f"properties missing required keys: {missing_keys}"
            )
            issue_rows.append(
                {
                    "issue_id": "issue_" + hashlib.md5(f"{exp_id}:missing_properties".encode()).hexdigest()[:12],
                    "source_record_id": None,
                    "rule_id": "google_experiment_properties_present",
                    "severity": "error",
                    "observed_value": f"folder={folder}",
                    "action": "rejected",
                    "reason": reason, 
                }
            )
            return exp_rows, trace_rows, issue_rows

        
        exp_rows.append(
            {
                "source_record_id": exp_rec_id,
                "experiment_id": exp_id,
                "basis": properties.get("basis", 0),
                "distance": properties.get("distance", 0),
                "rounds": properties.get("rounds", 0),
                "shots": properties.get("shots", 0),
                "center_row": properties.get("center_data_qubit_row", 0),
                "center_col": properties.get("center_data_qubit_col", 0),
                "measurement_count": properties.get("circuit_measurements", 0),
                "detector_count": properties.get("circuit_detectors", 0)
            }
        )
        trace_rows.append(
            {
                "source_record_id": exp_rec_id,
                "source_name": "google_qec",
                "bronze_object": "bronze/source=google_qec/google-surface-code-curated.zip",
                "archive_member": folder,
                "record_locator": f"experiment_index={exp_id}",
                "input_sha256": archive,
            }
        )
        return exp_rows, trace_rows, issue_rows

    @staticmethod
    def google_experiment_table(raw_bytes: bytes):
        folders = GoogleQecUtility.list_zip_folders(raw_bytes)
        all_experiment_rows = []
        all_trace_rows = []
        all_issue_rows = []
        for folder in folders:
            exp_rows, trace_rows, issue_rows = GoogleQecUtility.build_experiment_rows(raw_bytes, folder)
            all_experiment_rows.extend(exp_rows)
            all_trace_rows.extend(trace_rows)
            all_issue_rows.extend(issue_rows)

        experiment_df = pd.DataFrame(all_experiment_rows)
        if not experiment_df.empty:
            experiment_df = experiment_df.astype(
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
        issues_df = pd.DataFrame(
            all_issue_rows,
            columns=["issue_id", "source_record_id", "rule_id", "severity", "observed_value", "action", "reason"],
        )
        return experiment_df, trace_df, issues_df, len(folders)

    @staticmethod
    def google_shots_table(raw_bytes: bytes, accepted_experiment_ids: set):
        folders = GoogleQecUtility.list_zip_folders(raw_bytes)
        all_shot_rows = []
        all_trace_rows = []
        all_issue_rows = []
        shots_read = 0
        for folder in folders:
            shot_rows, trace_rows, issue_rows, declared_shots = GoogleQecUtility.build_shot_rows(
                raw_bytes, folder, accepted_experiment_ids
            )
            all_shot_rows.extend(shot_rows)
            all_trace_rows.extend(trace_rows)
            all_issue_rows.extend(issue_rows)
            shots_read += declared_shots

        shot_df = pd.DataFrame(all_shot_rows, columns=GoogleQecUtility.SHOT_COLUMNS).astype(
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
        trace_df = pd.DataFrame(columns=GoogleQecUtility.TRACE_COLUMNS)
        if len(all_trace_rows) > 0:
            trace_df = pd.concat(all_trace_rows, ignore_index=True)
        issues_df = pd.DataFrame(
            all_issue_rows,
            columns=["issue_id", "source_record_id", "rule_id", "severity", "observed_value", "action", "reason"],
        )
        return shot_df, trace_df, issues_df, shots_read


class QasmBenchUtility:
    BRONZE_KEY = "bronze/source=qasmbench/qasmbench-qec.zip"
    QASM_COMMENT = re.compile(r"//[^\n]*")
    QASM_GATE_BLOCK = re.compile(r"\bgate\b[^{};]*\{[^}]*\}", re.S)
    QASM_GATE_DEFINITION = re.compile(r"\bgate\s+(\w+)\s*([^{};]*)\{([^}]*)\}", re.S)
    QASM_QREG = re.compile(r"^qreg\s+(\w+)\s*\[\s*(\d+)\s*\]$")
    QASM_CREG = re.compile(r"^creg\s+(\w+)\s*\[\s*(\d+)\s*\]$")
    QASM_CONDITIONAL = re.compile(r"^if\s*\(\s*(\w+)\s*==\s*(\d+)\s*\)\s*(.+)$")
    QASM_OPERATION = re.compile(r"^([A-Za-z_]\w*)(\s*\([^)]*\))?\s+(.+)$")
    QASM_MEASURE = re.compile(r"^measure\s+(\S+)\s*->\s*(\w+)(?:\[\s*\d+\s*\])?$")
    QASM_INDEXED_QUBIT = re.compile(r"^(\w+)\s*\[\s*(\d+)\s*\]$")
    QASM_MEASURE_FULL = re.compile(r"^measure\s+(\S+)\s*->\s*(\S+)$")
    CIRCUIT_RULES = {
        "qasm_circuit_qreg_declared" : "circuit declares no quantum register",
        "qasm_circuit_measure_register_declared" : "measure addresses a register that is not declared",
        "qasm_circuit_measure_width_matches" : "register-addressed measure has mismatched qubit and bit widths",
        "qasm_circuit_operand_index_in_range" : "operand index is outside its declared register size",
        "qasm_circuit_known_gate" : "gate is neither defined in the circuit nor declared in qelib1.inc",
    }
    CONDITIONAL_CORRECTION_RULES = {
        "qasm_cc_condition_register_declared":"condition register is not a declared c_reg",
        "qasm_cc_condition_value_in_range":"condition value does not fit the declared register width",
        "qasm_cc_single_target":"the contract stores exactly one target_qubit",
        "qasm_cc_target_is_indexed_qubit":"target is a whole register, not one indexed qubit",
        "qasm_cc_target_register_declared":"target register is not a declared qreg",
        "qasm_cc_target_index_in_range":"target index is outside the declared register size",
        "qasm_cc_condition_register_measured":"condition register is never written by a measure",
        "qasm_cc_condition_before_measure":"condition appears before the measure that writes its register",
    }
    QASM_NOT_AN_OPERATION = {"OPENQASM", "include", "qreg", "creg", "gate", "opaque", "barrier"}
    QASM_BUILTIN_GATES = {"U", "CX"}
    # Gates a transpiler emits that this release's qelib1.inc does not
    # declare. sx is the square root of X, a standard basis gate on IBM
    # backends; the qelib1.inc shipped here predates it. Without this the
    # transpiled circuits raise 74 "unknown gate" warnings for a real gate.
    QASM_HARDWARE_BASIS_GATES = {"sx", "sxdg"}

    def __init__(self):
        super()


    @staticmethod
    def keep_line_breaks(match) -> str:
        return "\n" * match.group(0).count("\n")

    @staticmethod
    def qasm_statements(text: str) -> list[tuple[str, str]]:
        body = QasmBenchUtility.QASM_COMMENT.sub("", text)
        body = QasmBenchUtility.QASM_GATE_BLOCK.sub(QasmBenchUtility.keep_line_breaks, body)

        found: list[tuple[str, str]] = []
        ordinals: dict[int, int] = {}
        line, start_line, buffer = 1, None, []
        for character in body:
            if character == ";":
                statement = " ".join("".join(buffer).split())
                if statement:
                    ordinal = ordinals.get(start_line, 0)
                    ordinals[start_line] = ordinal + 1
                    found.append((f"L{start_line}.{ordinal}", statement))
                buffer, start_line = [], None
            else:
                if start_line is None and not character.isspace():
                    start_line = line
                buffer.append(character)
            if character == "\n":
                line += 1
        return found

    @staticmethod
    def qasm_registers(statements: list[tuple[str, str]]) -> tuple[dict, dict]:
        qregs: dict[str, int] = {}
        cregs: dict[str, int] = {}
        for _locator, statement in statements:
            qreg_match = QasmBenchUtility.QASM_QREG.match(statement)
            if qreg_match is not None:
                qregs[qreg_match.group(1)] = int(qreg_match.group(2))
                continue
            creg_match = QasmBenchUtility.QASM_CREG.match(statement)
            if creg_match is not None:
                cregs[creg_match.group(1)] = int(creg_match.group(2))
        return qregs, cregs

    @staticmethod
    def qasm_gate_definitions(text: str) -> dict[str, tuple[list[str], list[str]]]:
        body = QasmBenchUtility.QASM_COMMENT.sub("", text)
        definitions: dict[str, tuple[list[str], list[str]]] = {}
        for match in QasmBenchUtility.QASM_GATE_DEFINITION.finditer(body):
            parameters = []
            for parameter in match.group(2).split(","):
                parameter = parameter.strip()
                if parameter:
                    parameters.append(parameter)

            calls = []
            for call in match.group(3).split(";"):
                if call.strip():
                    calls.append(" ".join(call.split()))

            definitions[match.group(1)] = (parameters, calls)
        return definitions

    @staticmethod
    def qasm_circuit_identity(member: str) -> tuple[str, str, str]:
        benchmark = member.split("/")[1]
        if member.endswith("_transpiled.qasm"):
            variant = "transpiled"
        else:
            variant = "source"
        return f"qasmbench:{benchmark}:{variant}", benchmark, variant

    @staticmethod
    def qasm_operands(operation_text: str) -> list[str]:
        match = QasmBenchUtility.QASM_OPERATION.match(operation_text)
        if match is None:
            return []
        operands = []
        for operand in match.group(3).split(","):
            operand = operand.strip()
            if operand:
                operands.append(operand)
        return operands

    @staticmethod
    def qasm_expanded_operations(statements: list[tuple[str, str]], qregs: dict[str, int],gate_definitions: dict[str, tuple[list[str], list[str]]],) -> list[tuple[str, str, list[str]]]:
        operations = []

        # Work list instead of a recursive helper. A statement can unwrap into
        # more statements twice over: a conditional unwraps to the operation it
        # guards, and a macro call unwraps to its body. Anything we unwrap goes
        # back on the front of the list so the original order is kept.
        pending = list(statements)

        while pending:
            locator, statement = pending.pop(0)

            # `if (creg == int) <operation>;` is a wrapper, not a gate called
            # "if". Put the operation it guards back on the list.
            conditional = QasmBenchUtility.QASM_CONDITIONAL.match(statement)
            if conditional is not None:
                pending.insert(0, (locator, conditional.group(3).strip()))
                continue

            measure = QasmBenchUtility.QASM_MEASURE_FULL.match(statement)
            if measure is not None:
                qubit = measure.group(1)
                # `measure a -> syn` with qreg a[2] is two measurements
                if "[" not in qubit and qubit in qregs:
                    for index in range(qregs[qubit]):
                        operations.append((locator, "measure", [f"{qubit}[{index}]"]))
                else:
                    operations.append((locator, "measure", [qubit]))
                continue

            match = QasmBenchUtility.QASM_OPERATION.match(statement)
            if match is None:
                continue

            name = match.group(1)
            if name in QasmBenchUtility.QASM_NOT_AN_OPERATION:
                continue

            operands = QasmBenchUtility.qasm_operands(statement)

            # A gate defined in this circuit is a macro, so swap the caller's
            # qubits into its body and queue those statements up instead.
            if name in gate_definitions:
                parameters, body = gate_definitions[name]
                substitution = {}
                for index in range(len(parameters)):
                    if index < len(operands):
                        substitution[parameters[index]] = operands[index]

                replacements = []
                for body_statement in body:
                    rewritten = body_statement
                    for parameter in substitution:
                        pattern = r"\b" + re.escape(parameter) + r"\b"
                        rewritten = re.sub(pattern, substitution[parameter], rewritten)
                    replacements.append((locator, rewritten))

                pending[0:0] = replacements
                continue

            operations.append((locator, name, operands))

        return operations

    @staticmethod
    def qasm_expanded_statements(statements: list[tuple[str, str]], qregs: dict[str, int], cregs: dict[str, int], gate_definitions: dict[str, tuple[list[str], list[str]]],) -> list[tuple[str, str]]:
        expanded = []

        # Same work-list approach as qasm_expanded_operations above, but this
        # one hands back statement text because table 5 pattern-matches on it.
        pending = list(statements)

        while pending:
            locator, statement = pending.pop(0)

            measure = QasmBenchUtility.QASM_MEASURE_FULL.match(statement)
            if measure is not None:
                qubit = measure.group(1)
                bit = measure.group(2)
                whole_register = (
                    "[" not in qubit
                    and "[" not in bit
                    and qubit in qregs
                    and bit in cregs
                )
                if whole_register:
                    width = min(qregs[qubit], cregs[bit])
                    for index in range(width):
                        expanded.append(
                            (locator, f"measure {qubit}[{index}] -> {bit}[{index}]")
                        )
                else:
                    expanded.append((locator, statement))
                continue

            match = QasmBenchUtility.QASM_OPERATION.match(statement)
            if match is None:
                expanded.append((locator, statement))
                continue

            name = match.group(1)

            if name in gate_definitions:
                parameters, body = gate_definitions[name]
                operands = QasmBenchUtility.qasm_operands(statement)
                substitution = {}
                for index in range(len(parameters)):
                    if index < len(operands):
                        substitution[parameters[index]] = operands[index]

                replacements = []
                for body_statement in body:
                    rewritten = body_statement
                    for parameter in substitution:
                        pattern = r"\b" + re.escape(parameter) + r"\b"
                        rewritten = re.sub(pattern, substitution[parameter], rewritten)
                    replacements.append((locator, rewritten))

                pending[0:0] = replacements
                continue

            # a fence for the compiler, not something the circuit executes
            if name == "barrier":
                continue

            expanded.append((locator, statement))

        return expanded

    @staticmethod
    def qasm_known_gate_names(library_text: str, gate_definitions: dict) -> set[str]:
        known = set()
        known.update(QasmBenchUtility.qasm_gate_definitions(library_text))
        known.update(gate_definitions)
        known.update(QasmBenchUtility.QASM_BUILTIN_GATES)
        known.update(QasmBenchUtility.QASM_HARDWARE_BASIS_GATES)
        known.add("measure")
        known.add("reset")
        return known

    @staticmethod
    def list_qasmbench_circuits(raw_bytes: bytes) -> list[tuple[str, str, str]]:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            names = z.namelist()
        circuits = []
        for member in sorted(names):
            if not member.endswith(".qasm"):
                continue
            filename = member.rsplit("/", 1)[-1].removesuffix(".qasm")
            benchmark = member.rsplit("/", 2)[-2]
            if filename.endswith("_transpiled"):
                variant = "transpiled"
            else:
                variant = "source"
            circuits.append((benchmark, variant, member))
        return circuits

    @staticmethod
    def get_syndrome_registers(flat_statements):
        syndrome_registers = set()
        for _idx, stmt in flat_statements:
            if stmt.startswith("if("):
                reg_name = stmt.split("(")[1].split("==")[0].strip()
                syndrome_registers.add(reg_name)
        return syndrome_registers

    @staticmethod
    def find_stabilizer_checks(flat_statements):
        syndrome_registers = QasmBenchUtility.get_syndrome_registers(flat_statements)

        pending_targets = {}
        checks = []

        for idx, stmt in flat_statements:
            if stmt.startswith("cx"):
                qubits = stmt.replace("cx", "").strip().split(",")
                source, target = qubits[0].strip(), qubits[1].strip()
                if target not in pending_targets:
                    pending_targets[target] = ([], [])
                data_qubits, build_indices = pending_targets[target]
                data_qubits.append(source)
                build_indices.append(idx)

            elif stmt.startswith("measure"):
                qubit = stmt.split()[1]
                bit = stmt.split("->")[1].strip()
                reg_name = bit.split("[")[0]

                if qubit in pending_targets and reg_name in syndrome_registers:
                    data_qubits, build_indices = pending_targets[qubit]
                    checks.append({
                        "check_id": qubit,
                        "ancilla_qubit": qubit,
                        "data_qubits": sorted(set(data_qubits)),
                        "syndrome_bit": bit,
                        "build_stmt_indices": sorted(set(build_indices)),
                        "measure_stmt_index": idx,
                    })
                pending_targets.pop(qubit, None)

        return checks

    @staticmethod
    def extract_stabilizer_checks(qasm_text):
        statements = QasmBenchUtility.qasm_statements(qasm_text)
        qregs, cregs = QasmBenchUtility.qasm_registers(statements)
        registers = {}
        registers.update(qregs)
        registers.update(cregs)
        flat_statements = QasmBenchUtility.qasm_expanded_statements(
            statements, qregs, cregs, QasmBenchUtility.qasm_gate_definitions(qasm_text)
        )
        return QasmBenchUtility.find_stabilizer_checks(flat_statements), registers

    @staticmethod
    def validate_check(check, registers):
        problems = []

        if not check["data_qubits"]:
            problems.append(("stabilizer_check_has_data_qubits", check["ancilla_qubit"]))
        if check["ancilla_qubit"] in check["data_qubits"]:
            problems.append(("stabilizer_ancilla_not_data_qubit", check["ancilla_qubit"]))

        for qubit_ref in [check["ancilla_qubit"]] + check["data_qubits"]:
            try:
                reg, index_text = qubit_ref.split("[")
                idx = int(index_text.rstrip("]"))
            except ValueError:
                problems.append(("stabilizer_qubit_indexed", qubit_ref))
                continue
            if reg not in registers:
                problems.append(("stabilizer_register_declared", qubit_ref))
            elif idx >= registers[reg]:
                problems.append(("stabilizer_index_in_range", qubit_ref))

        syn_reg, syn_idx = check["syndrome_bit"].split("[")
        syn_idx = int(syn_idx.rstrip("]"))
        if syn_reg not in registers:
            problems.append(("stabilizer_syndrome_register_declared", check["syndrome_bit"]))
        elif syn_idx >= registers[syn_reg]:
            problems.append(("stabilizer_syndrome_bit_in_range", check["syndrome_bit"]))

        return problems

    @staticmethod
    def build_stabilizer_check_rows(raw_bytes: bytes, benchmark: str, variant: str, member: str, archive_sha256: str):
        circuit_id = f"qasmbench:{benchmark}:{variant}" 

        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            qasm_text = z.open(member).read().decode()

        checks, registers = QasmBenchUtility.extract_stabilizer_checks(qasm_text)

        check_rows = []  # storage for accepted check rows
        trace_rows = []  # storage for trace table
        issue_rows = []  # storage for rejected checks

        for check in checks:
            source_record_id = (
                f"qasmbench:{member}#{check['measure_stmt_index']}"
                f":check={check['ancilla_qubit']}"
            )
            problems = QasmBenchUtility.validate_check(check, registers)

            if problems:
                for rule_id, observed in problems:
                    issue_rows.append(
                        {
                            "issue_id": "issue_" + hashlib.md5(f"{source_record_id}:{rule_id}".encode()).hexdigest()[
                                :12],
                            "source_record_id": source_record_id,
                            "rule_id": rule_id,
                            "severity": "error",
                            "observed_value": str(observed),
                            "action": "rejected",
                            "reason": rule_id.replace("_", " "),
                        }
                    )
                continue

            check_rows.append(
                {
                    "source_record_id": source_record_id,
                    "circuit_id": circuit_id,
                    "check_id": check["check_id"],
                    "ancilla_qubit": check["ancilla_qubit"],
                    "data_qubits": check["data_qubits"],
                    "syndrome_bit": check["syndrome_bit"],
                }
            )

            for stmt_idx in sorted(set(check["build_stmt_indices"]) | {check["measure_stmt_index"]}):
                trace_rows.append(
                    {
                        "source_record_id": source_record_id,
                        "source_name": "qasmbench",
                        "bronze_object": "bronze/source=qasmbench/qasmbench-qec.zip",
                        "archive_member": member,
                        "record_locator": stmt_idx,
                        "input_sha256": archive_sha256,
                    }
                )

        return check_rows, trace_rows, issue_rows

    @staticmethod
    def qasmbench_stabilizer_check_table(raw_bytes: bytes):
        circuits = QasmBenchUtility.list_qasmbench_circuits(raw_bytes)
        archive = hashlib.sha256(raw_bytes).hexdigest()  # proof of the version

        all_check_rows = []
        all_trace_rows = []
        all_issue_rows = []
        candidate_count = 0

        for benchmark, variant, member in circuits:
            check_rows, trace_rows, issue_rows = QasmBenchUtility.build_stabilizer_check_rows(
                raw_bytes, benchmark, variant, member, archive
            )
            rejected_ids = set()
            for row in issue_rows:
                rejected_ids.add(row["source_record_id"])
            candidate_count += len(check_rows) + len(rejected_ids)
            all_check_rows.extend(check_rows)
            all_trace_rows.extend(trace_rows)
            all_issue_rows.extend(issue_rows)

        check_df = pd.DataFrame(
            all_check_rows,
            columns=["source_record_id", "circuit_id", "check_id", "ancilla_qubit", "data_qubits", "syndrome_bit"],
        ).astype(
            {
                "source_record_id": "string",
                "circuit_id": "string",
                "check_id": "string",
                "ancilla_qubit": "string",
                "syndrome_bit": "string",
            }
        )
        trace_df = pd.DataFrame(all_trace_rows)
        issues_df = pd.DataFrame(
            all_issue_rows,
            columns=["issue_id", "source_record_id", "rule_id", "severity", "observed_value", "action", "reason"],
        )

        return check_df, trace_df, issues_df, candidate_count

    @staticmethod
    def build_circuit_rows(raw_bytes: bytes, member: str,archive_sha256: str, library_text: str,):
        circuit_id, benchmark_name, variant_name = QasmBenchUtility.qasm_circuit_identity(member)

        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            qasm_text = z.open(member).read().decode()

        statements = QasmBenchUtility.qasm_statements(qasm_text)
        qregs, cregs = QasmBenchUtility.qasm_registers(statements)
        gate_definitions = QasmBenchUtility.qasm_gate_definitions(qasm_text)
        known_gates = QasmBenchUtility.qasm_known_gate_names(library_text, gate_definitions)

        source_record_id = f"qasmbench:{member}"
        problems = []

        if not qregs:
            problems.append(("qasm_circuit_qreg_declared", member, "error"))


        for _locator, statement in statements:
            measure = QasmBenchUtility.QASM_MEASURE_FULL.match(statement)
            if measure is None:
                continue
            qubit, bit = measure.group(1), measure.group(2)
            qubit_register = qubit.split("[")[0]
            bit_register = bit.split("[")[0]
            if qubit_register not in qregs or bit_register not in cregs:
                problems.append(("qasm_circuit_measure_register_declared", statement, "error"))
            elif "[" not in qubit and "[" not in bit:
                if qregs[qubit_register] != cregs[bit_register]:
                    problems.append((
                        "qasm_circuit_measure_width_matches",
                        f"{statement} ({qregs[qubit_register]} qubits -> "
                        f"{cregs[bit_register]} bits)",
                        "error",
                    ))

        operations = QasmBenchUtility.qasm_expanded_operations(statements, qregs, gate_definitions)


        for _locator, name, operands in operations:
            for operand in operands:
                indexed = QasmBenchUtility.QASM_INDEXED_QUBIT.match(operand)
                if indexed is None:
                    continue
                register, index = indexed.group(1), int(indexed.group(2))
                if register in qregs and index >= qregs[register]:
                    problems.append(("qasm_circuit_operand_index_in_range", operand, "error"))


        for _locator, name, _operands in operations:
            if name not in known_gates:
                problems.append(("qasm_circuit_known_gate", name, "warning"))

        issue_rows = []
        rejected = False
        for rule_id, observed, severity in problems:
            if severity == "error":
                action = "rejected"
                rejected = True
            else:
                action = "accepted"

            fingerprint = f"{source_record_id}:{rule_id}:{observed}"
            issue_rows.append({
                "issue_id": "issue_" + hashlib.md5(fingerprint.encode()).hexdigest()[:12],
                "source_record_id": source_record_id,
                "rule_id": rule_id,
                "severity": severity,
                "observed_value": str(observed),
                "action": action,
                "reason": QasmBenchUtility.CIRCUIT_RULES[rule_id],
            })

        # an error means the circuit never reaches Silver, but the issues do
        if rejected:
            return [], [], issue_rows


        register_declarations = ";".join(
            [f"qreg {name}[{size}]" for name, size in qregs.items()]
            + [f"creg {name}[{size}]" for name, size in cregs.items()]
        )

        circuit_rows = [
            {
                "source_record_id": source_record_id,
                "circuit_id": circuit_id,
                "benchmark_name": benchmark_name,
                "variant": variant_name,
                "register_declarations": register_declarations,
                "qubit_count": sum(qregs.values()),
                "measurement_count": sum(
                    1 for _l, name, _o in operations if name == "measure"
                ),
                "two_qubit_gate_count": sum(
                    1 for _l, name, operands in operations
                    if name != "measure" and len(operands) == 2
                ),
            }
        ]

        trace_rows = [
            {
                "source_record_id": source_record_id,
                "source_name": "qasmbench",
                "bronze_object": "bronze/source=qasmbench/qasmbench-qec.zip",
                "archive_member": member,
                "record_locator": "whole_member",
                "input_sha256": archive_sha256,
            }
        ]

        return circuit_rows, trace_rows, issue_rows

    @staticmethod
    def qasmbench_circuit_table(raw_bytes: bytes):
        circuits = QasmBenchUtility.list_qasmbench_circuits(raw_bytes)
        archive = hashlib.sha256(raw_bytes).hexdigest()  # proof of the version

        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            library_text = (
                z.read("qelib1.inc").decode() if "qelib1.inc" in z.namelist() else ""
            )

        all_circuit_rows = []
        all_trace_rows = []
        all_issue_rows = []

        for benchmark, variant, member in circuits:
            circuit_rows, trace_rows, issue_rows = QasmBenchUtility.build_circuit_rows(raw_bytes, member, archive, library_text)
            all_circuit_rows.extend(circuit_rows)
            all_trace_rows.extend(trace_rows)
            all_issue_rows.extend(issue_rows)

        circuit_df = pd.DataFrame(
            all_circuit_rows,
            columns=["source_record_id", "circuit_id", "benchmark_name", "variant",
                     "register_declarations", "qubit_count", "measurement_count",
                     "two_qubit_gate_count"],
        ).astype(
            {
                "source_record_id": "string",
                "circuit_id": "string",
                "benchmark_name": "string",
                "variant": "string",
                "register_declarations": "string",
                "qubit_count": "int32",
                "measurement_count": "int32",
                "two_qubit_gate_count": "int32",
            }
        ).sort_values(["benchmark_name", "variant"]).reset_index(drop=True)

        trace_df = pd.DataFrame(
            all_trace_rows,
            columns=["source_record_id", "source_name", "bronze_object",
                     "archive_member", "record_locator", "input_sha256"],
        )
        issues_df = pd.DataFrame(
            all_issue_rows,
            columns=["issue_id", "source_record_id", "rule_id", "severity",
                     "observed_value", "action", "reason"],
        )

        return circuit_df, trace_df, issues_df, len(circuits)

    @staticmethod
    def build_conditional_correction_rows(raw_bytes: bytes, member: str, archive_sha256: str):
        circuit_id, _benchmark, _variant = QasmBenchUtility.qasm_circuit_identity(member)

        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            qasm_text = z.open(member).read().decode()

        statements = QasmBenchUtility.qasm_statements(qasm_text)
        q_regs, c_regs = QasmBenchUtility.qasm_registers(statements)

        # where each statement sits in execution order, so we can tell whether
        # the measure that writes a register comes before the if that reads it
        position = {}
        index = 0
        for locator, _statement in statements:
            position[locator] = index
            index += 1
        measured_at = {}
        for locator, statement in statements:
            match = QasmBenchUtility.QASM_MEASURE.match(statement)
            if match is not None:
                measured_at.setdefault(match.group(2), position[locator])

        correction_rows = []  # storage for accepted correction rows
        trace_rows = []  # storage for trace table
        issue_rows = []  # storage for rejected corrections

        for locator, statement in statements:
            match = QasmBenchUtility.QASM_CONDITIONAL.match(statement)
            if match is None:
                continue

            register = match.group(1)
            value = int(match.group(2))
            operation = match.group(3).strip()
            source_record_id = f"qasmbench:{member}#{locator}"

            operation_match = QasmBenchUtility.QASM_OPERATION.match(operation)
            gate = None
            targets = []
            if operation_match is not None:
                gate = operation_match.group(1)
                for target in operation_match.group(3).split(","):
                    targets.append(target.strip())

            problems = []  # (rule_id, observed_value, severity)

            if register not in c_regs:
                problems.append(("qasm_cc_condition_register_declared", register, "error"))
            elif not 0 <= value < 2 ** c_regs[register]:
                problems.append(("qasm_cc_condition_value_in_range", value, "error"))

            if len(targets) != 1:
                problems.append(("qasm_cc_single_target", operation, "error"))
            else:
                target_match = QasmBenchUtility.QASM_INDEXED_QUBIT.match(targets[0])
                if target_match is None:
                    problems.append(("qasm_cc_target_is_indexed_qubit", targets[0], "error"))
                elif target_match.group(1) not in q_regs:
                    problems.append(("qasm_cc_target_register_declared", targets[0], "error"))
                elif int(target_match.group(2)) >= q_regs[target_match.group(1)]:
                    problems.append(("qasm_cc_target_index_in_range", targets[0], "error"))

            measured = measured_at.get(register)
            if register in c_regs and measured is None:
                problems.append(("qasm_cc_condition_register_measured", register, "warning"))
            elif measured is not None and measured > position[locator]:
                problems.append((
                    "qasm_cc_condition_before_measure",
                    f"measure at {measured}, condition at {position[locator]}",
                    "warning",
                ))

            rejected = False
            for _rule, _observed, severity in problems:
                if severity == "error":
                    rejected = True
                    break

            for rule_id, observed, severity in problems:
                issue_rows.append(
                    {
                        "issue_id": "issue_" + hashlib.md5(
                            f"{source_record_id}:{rule_id}".encode()
                        ).hexdigest()[:12],
                        "source_record_id": source_record_id,
                        "rule_id": rule_id,
                        "severity": severity,
                        "observed_value": str(observed),
                        "action": "rejected" if severity == "error" else "accepted",
                        "reason": QasmBenchUtility.CONDITIONAL_CORRECTION_RULES[rule_id],
                    }
                )

            if rejected:
                continue

            correction_rows.append(
                {
                    "source_record_id": source_record_id,
                    "circuit_id": circuit_id,
                    "condition_register": register,
                    "condition_value": value,
                    "gate": gate,
                    "target_qubit": targets[0],
                }
            )

            trace_rows.append(
                {
                    "source_record_id": source_record_id,
                    "source_name": "qasmbench",
                    "bronze_object": "bronze/source=qasmbench/qasmbench-qec.zip",
                    "archive_member": member,
                    "record_locator": locator,
                    "input_sha256": archive_sha256,
                }
            )

        return correction_rows, trace_rows, issue_rows

    @staticmethod
    def qasmbench_conditional_correction_table(raw_bytes: bytes):
        circuits = QasmBenchUtility.list_qasmbench_circuits(raw_bytes)
        archive = hashlib.sha256(raw_bytes).hexdigest()  # proof of the version

        all_correction_rows = []
        all_trace_rows = []
        all_issue_rows = []

        for benchmark, variant, member in circuits:
            correction_rows, trace_rows, issue_rows = (QasmBenchUtility.build_conditional_correction_rows(raw_bytes,
                                                                                                          member, archive))
            all_correction_rows.extend(correction_rows)
            all_trace_rows.extend(trace_rows)
            all_issue_rows.extend(issue_rows)

        # candidates are statements that looked like a correction, accepted or
        # not, so this reconciles against a plain text scan for "if("
        rejected_ids = set()
        for row in all_issue_rows:
            if row["action"] == "rejected":
                rejected_ids.add(row["source_record_id"])
        candidate_count = len(all_correction_rows) + len(rejected_ids)

        correction_df = pd.DataFrame(all_correction_rows,columns=["source_record_id", "circuit_id", "condition_register","condition_value", "gate", "target_qubit"],).astype({
            "source_record_id": "string",
            "circuit_id": "string",
            "condition_register": "string",
            "condition_value": "int64",
            "gate": "string",
            "target_qubit": "string",
        }).sort_values(["circuit_id", "condition_value"]).reset_index(drop=True)

        trace_df = pd.DataFrame(all_trace_rows, columns=["source_record_id", "source_name", "bronze_object", "archive_member", "record_locator", "input_sha256"])
        issues_df = pd.DataFrame(all_issue_rows, columns=["issue_id", "source_record_id", "rule_id", "severity","observed_value", "action", "reason"])

        return correction_df, trace_df, issues_df, candidate_count


class SyndromeUtility:
    BRONZE_KEY = "bronze/source=qec_syndromes/syndromes_dataset.zip"
    FILENAME_PATTERN = re.compile(r"d-(\d+)_pfr-([\d.]+)_nb-(\S+)\.csv")
    # Column names README.txt claims the CSVs have, e.g. "- label: binary label".
    DOCUMENTED_COLUMN_PATTERN = re.compile(r"^-\s*(\w+)\s*:", re.M)

    def __init__(self):
        pass

    @staticmethod
    def list_csv_members(raw_bytes: bytes) -> list[str]:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            names = z.namelist()
        csv_names = []
        for name in names:
            if name.endswith(".csv"):
                csv_names.append(name)
        return csv_names

    @staticmethod
    def parse_filename(csv_name: str) -> dict | None:
        match = SyndromeUtility.FILENAME_PATTERN.match(csv_name)
        if not match:
            return None
        distance, pfr, nb = match.groups()
        # fault_rate_text keeps the rate exactly as the filename spells it.
        # Formatting the float instead puts scientific notation into
        # experiment_id: float("0.000010") renders as "1e-05".
        return {
            "distance": int(distance),
            "physical_fault_rate": float(pfr),
            "fault_rate_text": pfr,
            "nb": nb,
        }

    @staticmethod
    def parse_syndrome_bits(raw_value: str) -> bytes | None:
        try:
            rounds = ast.literal_eval(raw_value)
        except (ValueError, SyntaxError):
            return None
        if not isinstance(rounds, tuple) or len(rounds) != 4:
            return None
        flat_bits = []
        for round_values in rounds:
            if not isinstance(round_values, tuple) or len(round_values) != 4:
                return None
            for bit in round_values:
                if bit not in (0, 1):
                    return None
                flat_bits.append(bit)
        return bytes(flat_bits)

    @staticmethod
    def read_documented_columns(raw_bytes: bytes) -> list[str]:
        """Column names the bundled README.txt says the CSVs have."""
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            if "README.txt" not in z.namelist():
                return []
            readme = z.read("README.txt").decode("utf-8", "replace")
        return SyndromeUtility.DOCUMENTED_COLUMN_PATTERN.findall(readme)

    @staticmethod
    def check_documented_header(
        raw_bytes: bytes, actual_columns: list[str], run_id: str
    ) -> list[dict]:
        """Compare the documented column names against the real CSV header.

        brief.md lists this as a minimum check: the bundled README.txt
        documents the first column as `label`, while every CSV header actually
        says `labels`. The data is usable either way, so this is a warning that
        stays visible rather than a reason to reject rows.
        """
        documented = SyndromeUtility.read_documented_columns(raw_bytes)
        if not documented or documented == list(actual_columns):
            return []

        missing = []
        for name in documented:
            if name not in actual_columns:
                missing.append(name)

        unexpected = []
        for name in actual_columns:
            if name not in documented:
                unexpected.append(name)
        return [
            {
                "issue_id": "iss_" + hashlib.md5(
                    f"documented_header:{documented}:{list(actual_columns)}".encode()
                ).hexdigest()[:12],
                "run_id": run_id,
                "source_record_id": None,
                "rule_id": "syndrome_documented_header_matches",
                "severity": "warning",
                "observed_value": (
                    f"README.txt documents {documented}; "
                    f"CSV header is {list(actual_columns)}"
                ),
                "action": "accepted",
                "reason": (
                    f"documented column names differ from the header "
                    f"(documented only: {missing}; header only: {unexpected}); "
                    f"the header is authoritative and the rows are kept"
                ),
            }
        ]

    @staticmethod
    def build_rows(raw_bytes: bytes, csv_name: str, archive: str, run_id: str):
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            with z.open(csv_name) as f:
                raw_df = pd.read_csv(f)

        if "labels" not in raw_df.columns:
            raise ValueError(f"{csv_name}: expected header 'labels', found {list(raw_df.columns)}")

        filename_meta = SyndromeUtility.parse_filename(csv_name)
        if filename_meta is None:
            raise ValueError(f"Cannot parse experiment info from filename: {csv_name}")

        experiment_id = (
            f"syndrome_d{filename_meta['distance']}"
            f"_pfr{filename_meta['fault_rate_text']}"
        )
        archive_hash = hashlib.sha256(raw_bytes).hexdigest()

        rows, issue_rows, trace_rows = [], [], []
        total_quantity = 0

        for row_index, row in raw_df.iterrows():
            locator = f"{csv_name}:row={row_index}"
            source_record_id = f"syn_{hashlib.md5(f'{archive}:{locator}'.encode()).hexdigest()[:12]}"

            syndrome_bits = SyndromeUtility.parse_syndrome_bits(row["syndromes"])
            quantity = row["quantity"]

            reject_reason = None
            if syndrome_bits is None:
                reject_reason = "malformed or wrong-shape syndromes value"
            elif not (isinstance(quantity, (int, float)) and quantity > 0):
                reject_reason = "quantity must be > 0"

            if reject_reason:
                issue_rows.append({
                    "issue_id": f"iss_{hashlib.md5((locator + reject_reason).encode()).hexdigest()[:12]}",
                    "run_id": run_id,
                    "source_record_id": None,
                    "rule_id": "syndrome_observation_row_check",
                    "severity": "error",
                    "observed_value": str(row.to_dict()),
                    "action": "rejected",
                    "reason": reject_reason,
                })
                continue

            total_quantity += int(quantity)
            rows.append({
                "source_record_id": source_record_id,
                "experiment_id": experiment_id,
                "physical_fault_rate": filename_meta["physical_fault_rate"],
                "syndrome_bits": syndrome_bits,
                "round_count": 4,
                "check_count": 4,
                "logical_error_label": bool(row["labels"]),
                "quantity": int(quantity),
            })
            trace_rows.append({
                "source_record_id": source_record_id,
                "source_name": "qec_syndromes",
                "bronze_object": archive,
                "archive_member": csv_name,
                "record_locator": locator,
                "input_sha256": archive_hash,
            })

        expected_nb = filename_meta["nb"]
        if expected_nb.endswith("M"):
            expected_total = int(float(expected_nb[:-1]) * 1_000_000)
            if total_quantity != expected_total:
                issue_rows.append({
                    "issue_id": f"iss_{hashlib.md5(f'{csv_name}_total'.encode()).hexdigest()[:12]}",
                    "run_id": run_id,
                    "source_record_id": None,
                    "rule_id": "syndrome_file_total_quantity",
                    "severity": "warning",
                    "observed_value": str(total_quantity),
                    "action": "flagged",
                    "reason": f"expected {expected_total} weighted observations, got {total_quantity}",
                })

        return rows, issue_rows, trace_rows

    @staticmethod
    def syndrome_table(raw_bytes: bytes, archive: str, run_id: str):
        csv_names = SyndromeUtility.list_csv_members(raw_bytes)
        all_rows, all_issues, all_trace, rows_read = [], [], [], 0

        # The documented-header mismatch is a property of the release, not of
        # any one CSV, so it is recorded once rather than seven times.
        if csv_names:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
                with z.open(csv_names[0]) as f:
                    header_columns = list(pd.read_csv(f, nrows=0).columns)
            all_issues.extend(
                SyndromeUtility.check_documented_header(
                    raw_bytes, header_columns, run_id
                )
            )

        for csv_name in csv_names:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
                with z.open(csv_name) as f:
                    rows_read += len(pd.read_csv(f))
            rows, issues, trace = SyndromeUtility.build_rows(raw_bytes, csv_name, archive, run_id)
            all_rows.extend(rows)
            all_issues.extend(issues)
            all_trace.extend(trace)

        df = pd.DataFrame(all_rows).astype({
            "source_record_id": "string", "experiment_id": "string",
            "physical_fault_rate": "float64", "round_count": "int32",
            "check_count": "int32", "logical_error_label": "bool", "quantity": "int64",
        })
        return df, pd.DataFrame(all_issues), pd.DataFrame(all_trace), rows_read


def run(run_id: str) -> StageResult:
    result = StageResult(stage="prepare_data", run_id=run_id)
    issues_columns = ["issue_id", "run_id", "source_record_id", "rule_id", "severity", "observed_value", "action", "reason"]
    settings = Settings.from_environment()

    os.makedirs("silver/google_qec", exist_ok=True)
    os.makedirs("silver/qasmbench", exist_ok=True)
    os.makedirs("silver/qec_syndromes", exist_ok=True)
    os.makedirs("results/part1", exist_ok=True)

    # google_qec
    google_raw = fetch_bronze_bytes(settings, GoogleQecUtility.BRONZE_KEY)
    df_exp, df_trace_experiment, df_issues_experiment, exp_dirs = GoogleQecUtility.google_experiment_table(google_raw)
    accepted_experiment_ids = set()
    if not df_exp.empty:
        accepted_experiment_ids = set(df_exp["experiment_id"])
    df_shot, df_trace_shots, df_issues_shots, shot_count = GoogleQecUtility.google_shots_table(google_raw, accepted_experiment_ids)


    # QEC_Syndromes
    syn_raw = fetch_bronze_bytes(settings, SyndromeUtility.BRONZE_KEY)
    df_syn, df_syn_issues, df_syn_trace, syn_rows_read = SyndromeUtility.syndrome_table(syn_raw, SyndromeUtility.BRONZE_KEY, run_id)

    # QASM
    qasmbench_raw_bytes = fetch_bronze_bytes(settings, QasmBenchUtility.BRONZE_KEY)
    df_circuit, df_trace_circuit, df_issues_circuit, circuit_candidates = QasmBenchUtility.qasmbench_circuit_table(qasmbench_raw_bytes)
    df_stabilizer, df_trace_stabilizer, df_issues_stabilizer, stabilizer_candidates = QasmBenchUtility.qasmbench_stabilizer_check_table(qasmbench_raw_bytes)
    df_correction, df_trace_correction_table, df_issues_correction_table, correction_candidates = QasmBenchUtility.qasmbench_conditional_correction_table(qasmbench_raw_bytes)



    result.input_count = exp_dirs + shot_count + stabilizer_candidates + circuit_candidates + correction_candidates + syn_rows_read
    result.output_count = len(df_exp) + len(df_shot) + len(df_stabilizer) + len(df_circuit) + len(df_correction) + len(df_syn)

 




    df_trace = pd.concat([df_trace_experiment, df_trace_shots, df_syn_trace, df_trace_stabilizer, df_trace_circuit, df_trace_correction_table], axis=0, ignore_index=True)
    df_issues = pd.concat([df_issues_experiment, df_issues_shots, df_syn_issues, df_issues_stabilizer,df_issues_circuit, df_issues_correction_table], ignore_index=True)
    df_issues["run_id"] = run_id
    result.issue_count = len(df_issues)

    write_parquet(df_exp.reset_index(drop=True), "silver/google_qec/experiment.parquet")
    write_parquet(df_shot.reset_index(drop=True), "silver/google_qec/shot.parquet")
    write_parquet(df_trace.reset_index(drop=True), "results/part1/source_trace.parquet")
    write_parquet(df_issues.reset_index(drop=True), "results/part1/data_issues.parquet")
    write_parquet(df_syn.reset_index(drop=True), "silver/qec_syndromes/syndrome_observation.parquet")
    write_parquet(df_correction.reset_index(drop=True), "silver/qasmbench/conditional_correction.parquet")
    write_parquet(df_circuit.reset_index(drop=True), "silver/qasmbench/circuit.parquet")
    write_parquet(df_stabilizer.reset_index(drop=True), "silver/qasmbench/stabilizer_check.parquet")

    row_counts = {
        "experiment": {"read": exp_dirs, "accepted": len(df_exp), "rejected": exp_dirs - len(df_exp)},
        "shot": {"read": shot_count, "accepted": len(df_shot), "rejected": shot_count - len(df_shot)},
        "syndrome_observation": {"read": syn_rows_read, "accepted": len(df_syn), "rejected": syn_rows_read - len(df_syn),},
        "conditional_correction": {"read": correction_candidates, "accepted": len(df_correction), "rejected": correction_candidates - len(df_correction),},
        "circuit": {"read": circuit_candidates, "accepted": len(df_circuit), "rejected": circuit_candidates - len(df_circuit),},
        "stabilizer_check": {"read": stabilizer_candidates, "accepted": len(df_stabilizer), "rejected": stabilizer_candidates - len(df_stabilizer),}
    }
    with open("results/part1/row_counts.json", "w") as f:
        json.dump(row_counts, f, indent=2)



    filesToBeUploaded = [
        "silver/google_qec/experiment.parquet",
        "silver/google_qec/shot.parquet",
        "silver/qasmbench/circuit.parquet",
        "silver/qasmbench/stabilizer_check.parquet",
        "silver/qasmbench/conditional_correction.parquet",
        "silver/qec_syndromes/syndrome_observation.parquet",
        "results/part1/source_trace.parquet",
        "results/part1/data_issues.parquet",
        "results/part1/row_counts.json",
    ]
    client = minio_client(settings)
    for path in filesToBeUploaded:
        if os.path.exists(path):
            client.fput_object(bucket_name=settings.s3_bucket, object_name=path, file_path=path)




    result.finish()
    return result