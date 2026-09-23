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
import re
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

        # ok = Utility.check_equal_lengths(
        #     measurements=measurements,
        #     detections=detections,
        #     sweeps=sweeps,
        #     actual_flips=actual_flips,
        #     belief=belief,
        #     correlated=correlated,
        #     pymatching=pymatching,
        #     tensor_net=tensor_net,
        # )
        # if not ok:
        #     raise ValueError(f"Companion file length mismatch in {folder}")

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
                    "record_locator": f"shot_index={shot_index}",
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
                "record_locator": f"experiment_index={exp_id}",
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
        
    #----------------------------------Sahil's Code Starts here---------------------------------------------------------
    @staticmethod
    def fetch_qasmbench_raw_bytes(settings) -> bytes:
        client = minio_client(settings)  # connection
        key = "bronze/source=qasmbench/qasmbench-qec.zip"
        response = client.get_object(settings.s3_bucket, key)  # retrieve the dataset
        raw_bytes = response.read()  # load all the data into RAM
        response.close()
        response.release_conn()  # return the connection
        return raw_bytes
     
     
    @staticmethod
    def list_qasmbench_circuits(raw_bytes: bytes) -> list[tuple[str, str, str]]:
        """Every .qasm circuit in the archive as (benchmark, variant, member).
        Same idea as list_zip_folders -- discover what to loop over from the
        archive itself instead of hardcoding it."""
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            names = z.namelist()
        circuits = []
        for member in sorted(names):
            if not member.endswith(".qasm"):
                continue
            filename = member.rsplit("/", 1)[-1].removesuffix(".qasm")
            benchmark = member.rsplit("/", 2)[-2]
            variant = "transpiled" if filename.endswith("_transpiled") else "source"
            circuits.append((benchmark, variant, member))
        return circuits
     
     
    @staticmethod
    def clean_lines(text):
        """Strip comments and blank lines out of the raw QASM text."""
        lines = []
        for line in text.split("\n"):
            line = line.split("//")[0].strip()   # drop comments, drop whitespace
            if line:                              # skip blank lines
                lines.append(line)
        return lines
     
     
    @staticmethod
    def get_registers(lines):
        """Collect qreg/creg name -> declared size."""
        registers = {}
        for line in lines:
            if line.startswith("qreg") or line.startswith("creg"):
                name = line.split()[1].split("[")[0]
                size = int(line.split("[")[1].split("]")[0])
                registers[name] = size
        return registers
     
     
    @staticmethod
    def get_gate_definition(lines):
        """Find the one custom `gate NAME params { body }` block, if any."""
        for i, line in enumerate(lines):
            if line.startswith("gate "):
                header = line.split("{")[0]
                gate_name = header.split()[1]
                params = header.split()[2].split(",")
     
                body_text = ""
                for body_line in lines[i:]:
                    body_text += body_line
                    if "}" in body_line:
                        break
                body_text = body_text.split("{", 1)[1].split("}", 1)[0]
                body = [s.strip() for s in body_text.split(";") if s.strip()]
                return gate_name, params, body
        return None, None, None
     
     
    @staticmethod
    def expand_gate_call(call_line, gate_name, params, body):
        """Substitute real qubit names into a custom gate's body at its call site."""
        args = call_line.split(gate_name, 1)[1].strip(" ;").split(",")
        args = [a.strip() for a in args]
        substitution = dict(zip(params, args))
     
        expanded = []
        for stmt in body:
            for param, real_qubit in substitution.items():
                # \b...\b avoids accidentally replacing "q" inside "q2" etc.
                stmt = re.sub(rf"\b{re.escape(param)}\b", real_qubit, stmt)
            expanded.append(stmt)
        return expanded
     
     
    @staticmethod
    def get_flat_statements(lines, registers):
        """Flatten declarations/gate-calls/whole-register measures into a single
        list of (statement_index, statement) pairs, expanding the custom gate
        body inline and expanding whole-register `measure a -> syn;` into one
        entry per qubit/bit pair. `barrier` is skipped -- it's a compiler fence,
        not an executed operation, so it must not count as a statement."""
        gate_name, params, body = Utility.get_gate_definition(lines)
     
        flat = []
        inside_gate_def = False
        stmt_index = 0
        for line in lines:
            if line.startswith("OPENQASM") or line.startswith("include"):
                stmt_index += 1
                continue
            if line.startswith("qreg") or line.startswith("creg"):
                stmt_index += 1
                continue
            if line.startswith("gate "):
                inside_gate_def = "}" not in line
                stmt_index += 1
                continue
            if inside_gate_def:
                if "}" in line:
                    inside_gate_def = False
                continue
            if line.startswith("barrier"):
                continue
     
            for stmt in line.split(";"):
                stmt = stmt.strip()
                if not stmt:
                    continue
     
                this_index = stmt_index
                stmt_index += 1
     
                if gate_name and stmt.startswith(gate_name + " "):
                    for expanded_stmt in Utility.expand_gate_call(stmt, gate_name, params, body):
                        flat.append((this_index, expanded_stmt))
                    continue
     
                if stmt.startswith("measure"):
                    qubit_arg = stmt.split()[1]
                    bit_arg = stmt.split("->")[1].strip()
                    if "[" not in qubit_arg and qubit_arg in registers:
                        size = registers[qubit_arg]
                        for i in range(size):
                            flat.append((this_index, f"measure {qubit_arg}[{i}] -> {bit_arg}[{i}]"))
                        continue
     
                flat.append((this_index, stmt))
        return flat
     
     
    @staticmethod
    def get_syndrome_registers(flat_statements):
        """Classical registers that are actually read by an if(reg==value) --
        a wire only counts as a real syndrome ancilla if it feeds one of these."""
        syndrome_registers = set()
        for _idx, stmt in flat_statements:
            if stmt.startswith("if("):
                reg_name = stmt.split("(")[1].split("==")[0].strip()
                syndrome_registers.add(reg_name)
        return syndrome_registers
     
     
    @staticmethod
    def find_stabilizer_checks(flat_statements):
        """Walk the flattened statements once: a cx accumulates a data qubit
        against its target (candidate ancilla); a measure of that target into a
        register some `if` reads turns it into a confirmed stabilizer check."""
        syndrome_registers = Utility.get_syndrome_registers(flat_statements)
     
        pending_targets = {}       # ancilla qubit -> (data qubits, build stmt indices)
        checks = []
     
        for idx, stmt in flat_statements:
            if stmt.startswith("cx"):
                qubits = stmt.replace("cx", "").strip().split(",")
                source, target = qubits[0].strip(), qubits[1].strip()
                data_qubits, build_indices = pending_targets.setdefault(target, ([], []))
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
        """Top-level parse: raw QASM text in, (checks, registers) out."""
        lines = Utility.clean_lines(qasm_text)
        registers = Utility.get_registers(lines)
        flat_statements = Utility.get_flat_statements(lines, registers)
        return Utility.find_stabilizer_checks(flat_statements), registers
     
     
    @staticmethod
    def validate_check(check, registers):
        """Reject a malformed check with a reason instead of silently accepting
        it -- these reasons become rows in data_issues.parquet."""
        problems = []
     
        def reg_and_index(qubit_ref):
            name, idx = qubit_ref.split("[")
            return name, int(idx.rstrip("]"))
     
        if not check["data_qubits"]:
            problems.append(("stabilizer_check_has_data_qubits", check["ancilla_qubit"]))
        if check["ancilla_qubit"] in check["data_qubits"]:
            problems.append(("stabilizer_ancilla_not_data_qubit", check["ancilla_qubit"]))
     
        for qubit_ref in [check["ancilla_qubit"], *check["data_qubits"]]:
            try:
                reg, idx = reg_and_index(qubit_ref)
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
        """Parses ONE circuit. Returns (check_rows, trace_rows, issue_rows) --
        mirrors build_experiment_rows(raw_bytes, folder)."""
        circuit_id = f"qasmbench:{benchmark}:{variant}"  # matches table 6's convention
     
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            qasm_text = z.open(member).read().decode()
     
        checks, registers = Utility.extract_stabilizer_checks(qasm_text)
     
        check_rows = []  # storage for accepted check rows
        trace_rows = []  # storage for trace table
        issue_rows = []  # storage for rejected checks
     
        for check in checks:
            source_record_id = (
                f"qasmbench:{member}#stmt={check['measure_stmt_index']}"
                f":check={check['ancilla_qubit']}"
            )
            problems = Utility.validate_check(check, registers)
     
            if problems:
                for rule_id, observed in problems:
                    issue_rows.append(
                        {
                            "issue_id": "issue_" + hashlib.md5(f"{source_record_id}:{rule_id}".encode()).hexdigest()[:12],
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
                        "record_locator": f"stmt={stmt_idx}",
                        "input_sha256": archive_sha256,
                    }
                )
     
        return check_rows, trace_rows, issue_rows
     
     
    @staticmethod
    def qasmbench_stabilizer_check_table(raw_bytes: bytes):
        """Loops over every circuit found in the archive. Returns (check_df,
        trace_df, issues_df, candidate_count) -- mirrors
        google_experiment_table(raw_bytes)."""
        circuits = Utility.list_qasmbench_circuits(raw_bytes)
        archive = hashlib.sha256(raw_bytes).hexdigest()  # proof of the version
     
        all_check_rows = []
        all_trace_rows = []
        all_issue_rows = []
        candidate_count = 0
     
        for benchmark, variant, member in circuits:
            check_rows, trace_rows, issue_rows = Utility.build_stabilizer_check_rows(
                raw_bytes, benchmark, variant, member, archive
            )
            candidate_count += len(check_rows) + len(issue_rows)
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



def run(run_id: str) -> StageResult:
    result = StageResult(stage="prepare_data", run_id=run_id)
 
    settings = Settings.from_environment()
    os.makedirs("silver/google_qec", exist_ok=True)
    os.makedirs("silver/qasmbench", exist_ok=True)
    os.makedirs("results/part1", exist_ok=True)
 
    raw_bytes = Utility.fetch_raw_data_bytes(settings)
 
    df_exp, df_trace1, exp_dirs = Utility.google_experiment_table(raw_bytes)
    df_shot, df_trace2, shot_count = Utility.google_shots_table(raw_bytes)
 
    df_trace = pd.concat([df_trace1, df_trace2], axis=0, ignore_index=True)
 
    issues_columns = ["issue_id", "run_id", "source_record_id", "rule_id",
                      "severity", "observed_value", "action", "reason"]
    df_issues = pd.DataFrame(columns=issues_columns)  # 0 rows right now -- nothing was rejected
 
    row_counts = {
        "experiment": {"read": exp_dirs, "accepted": len(df_exp), "rejected": exp_dirs - len(df_exp)},
        "shot": {"read": shot_count, "accepted": len(df_shot), "rejected": shot_count - len(df_shot)},
    }
 
    upload_map = {
        "silver/google_qec/experiment.parquet": "silver/google_qec/experiment.parquet",
        "silver/google_qec/shot.parquet": "silver/google_qec/shot.parquet",
        "results/part1/source_trace.parquet": "results/part1/source_trace.parquet",
        "results/part1/data_issues.parquet": "results/part1/data_issues.parquet",
    }
 
    result.input_count = exp_dirs + shot_count
    result.output_count = len(df_exp) + len(df_shot)
    result.issue_count = (exp_dirs + shot_count) - (len(df_exp) + len(df_shot))
 
    # --- table 5 wiring: stabilizer_check ---
    qasmbench_raw_bytes = Utility.fetch_qasmbench_raw_bytes(settings)
    df_stabilizer, df_trace3, df_issues3, stabilizer_candidates = (
        Utility.qasmbench_stabilizer_check_table(qasmbench_raw_bytes)
    )
    df_stabilizer.to_parquet("silver/qasmbench/stabilizer_check.parquet")
 
    df_trace = pd.concat([df_trace, df_trace3], axis=0, ignore_index=True)
    df_issues = pd.concat([df_issues, df_issues3], axis=0, ignore_index=True)
 
    row_counts["stabilizer_check"] = {
        "read": stabilizer_candidates,
        "accepted": len(df_stabilizer),
        "rejected": stabilizer_candidates - len(df_stabilizer),
    }
    upload_map["silver/qasmbench/stabilizer_check.parquet"] = "silver/qasmbench/stabilizer_check.parquet"
 
    result.input_count += stabilizer_candidates
    result.output_count += len(df_stabilizer)
    result.issue_count += stabilizer_candidates - len(df_stabilizer)
    # --- end table 5 wiring ---
 
    df_exp.to_parquet("silver/google_qec/experiment.parquet")
    df_shot.to_parquet("silver/google_qec/shot.parquet")
    df_trace.to_parquet("results/part1/source_trace.parquet")
    df_issues.to_parquet("results/part1/data_issues.parquet")
 
    with open("results/part1/row_counts.json", "w") as f:
        json.dump(row_counts, f, indent=2)
 
    client = minio_client(settings)
    for local_file, s3_key in upload_map.items():
        client.fput_object(
            bucket_name=settings.s3_bucket,
            object_name=s3_key,
            file_path=local_file
        )
 
    result.finish()
    return result