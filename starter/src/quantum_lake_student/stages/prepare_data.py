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


# ============================================================================
# Shared OpenQASM parsing -- used by tables 4 (circuit), 5 (stabilizer_check)
# and 6 (conditional_correction).
#
# Why this is module-level and shared: each QASMBench table previously had its
# own statement counter, and they disagreed. On qec_sm_n5_transpiled.qasm the
# same `if(syn==1) x q[0];` line was statement 13 to one parser and 14 to
# another, so the two tables produced different source_record_id values for the
# same Bronze statement. Nothing had been written to Silver yet when this was
# found, so the fix was free.
#
# Locators here are LINE-based ("L17.0" = line 17, first statement on it)
# rather than an index into a filtered statement list. A line number is a
# property of the file, so it does not move when a parser changes its mind
# about what counts as a statement, and a grader can check it by opening the
# file and counting. The ordinal suffix is needed because OpenQASM allows
# several statements on one line: `cx d1,a1; cx d2,a1;`.
# ============================================================================

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


def qasm_statements(text: str) -> list[tuple[str, str]]:
    """Executable statements as (locator, statement_text).

    Comments are stripped and `gate NAME(...) { ... }` definition bodies are
    excluded: a gate body is a definition, not executed code -- only its call
    site executes. Gate blocks are replaced by their own newline count so the
    line numbers of everything after them stay correct.
    """
    body = QASM_COMMENT.sub("", text)
    body = QASM_GATE_BLOCK.sub(lambda m: "\n" * m.group(0).count("\n"), body)

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


def qasm_registers(statements: list[tuple[str, str]]) -> tuple[dict, dict]:
    """(qregs, cregs) as name -> declared size.

    Kept separate because register-local indexing matters: q[0] and a[0] are
    different qubits, so a qubit reference is only meaningful together with its
    register name.
    """
    qregs: dict[str, int] = {}
    cregs: dict[str, int] = {}
    for _locator, statement in statements:
        if (match := QASM_QREG.match(statement)) is not None:
            qregs[match.group(1)] = int(match.group(2))
        elif (match := QASM_CREG.match(statement)) is not None:
            cregs[match.group(1)] = int(match.group(2))
    return qregs, cregs


def qasm_gate_definitions(text: str) -> dict[str, tuple[list[str], list[str]]]:
    """Custom gate definitions as name -> (parameter names, body statements).

    Table 4 needs this to count the operations a call site really executes;
    table 5 needs it to see the parity checks hidden inside `gate syndrome`.
    """
    body = QASM_COMMENT.sub("", text)
    definitions: dict[str, tuple[list[str], list[str]]] = {}
    for match in QASM_GATE_DEFINITION.finditer(body):
        parameters = [p.strip() for p in match.group(2).split(",") if p.strip()]
        calls = [" ".join(s.split()) for s in match.group(3).split(";") if s.strip()]
        definitions[match.group(1)] = (parameters, calls)
    return definitions


def qasm_circuit_identity(member: str) -> tuple[str, str, str]:
    """(circuit_id, benchmark_name, variant) for a .qasm archive member.

    circuit_id is the foreign key every QASMBench Silver table shares, so it is
    derived in exactly one place.
    """
    benchmark = member.split("/")[1]
    variant = "transpiled" if member.endswith("_transpiled.qasm") else "source"
    return f"qasmbench:{benchmark}:{variant}", benchmark, variant


def qasm_condition_trigger_bits(register: str, value: int, width: int) -> list[str]:
    """Which syndrome bits a condition value requires to be set.

    `if(syn==3)` on `creg syn[2]` means syn[0] and syn[1] both fired. Not part
    of the Silver contract -- the six required columns stay exactly as
    specified -- but Gold uses this to join a correction to the stabilizer
    checks that triggered it, which is analysis question 3.
    """
    return [f"{register}[{bit}]" for bit in range(width) if value >> bit & 1]


QASM_NOT_AN_OPERATION = {"OPENQASM", "include", "qreg", "creg", "gate", "opaque", "barrier"}
QASM_BUILTIN_GATES = {"U", "CX"}


def qasm_operands(operation_text: str) -> list[str]:
    """Comma-separated operands of an operation, parameters removed."""
    match = QASM_OPERATION.match(operation_text)
    if match is None:
        return []
    return [operand.strip() for operand in match.group(3).split(",") if operand.strip()]


def qasm_expanded_operations(
    statements: list[tuple[str, str]],
    qregs: dict[str, int],
    gate_definitions: dict[str, tuple[list[str], list[str]]],
) -> list[tuple[str, str, list[str]]]:
    """Executed operations as (locator, gate_name, operands).

    Expands the two things the QASM text hides, both of which silver-tables.md
    requires for table 4 ("count the expanded executed operations, not only the
    number of QASM statement lines"):

      * a call to a circuit-local `gate` macro becomes the operations in its
        body, with the caller's qubits substituted at the call site;
      * a register-addressed operation becomes one operation per qubit, so
        `measure a -> syn` with `qreg a[2]` is two measurements.

    `barrier` is excluded -- a compiler fence, not an executed operation.
    Library gates such as `cx` are NOT expanded into their U/CX definitions: a
    cx is one two-qubit operation, not its decomposition. Only gates defined in
    this circuit are macros.
    """
    operations: list[tuple[str, str, list[str]]] = []

    def emit(locator: str, statement: str) -> None:
        measure = QASM_MEASURE_FULL.match(statement)
        if measure is not None:
            qubit, bit = measure.group(1), measure.group(2)
            if "[" not in qubit and qubit in qregs:          # whole register
                for index in range(qregs[qubit]):
                    operations.append((locator, "measure", [f"{qubit}[{index}]"]))
            else:
                operations.append((locator, "measure", [qubit]))
            return

        match = QASM_OPERATION.match(statement)
        if match is None:
            return
        name = match.group(1)
        if name in QASM_NOT_AN_OPERATION:
            return

        operands = qasm_operands(statement)

        if name in gate_definitions:                          # circuit-local macro
            parameters, body = gate_definitions[name]
            substitution = dict(zip(parameters, operands))
            for body_statement in body:
                rewritten = body_statement
                for parameter, actual in substitution.items():
                    rewritten = re.sub(rf"\b{re.escape(parameter)}\b", actual, rewritten)
                emit(locator, rewritten)
            return

        operations.append((locator, name, operands))

    for locator, statement in statements:
        emit(locator, statement)
    return operations


def qasm_expanded_statements(
    statements: list[tuple[str, str]],
    qregs: dict[str, int],
    cregs: dict[str, int],
    gate_definitions: dict[str, tuple[list[str], list[str]]],
) -> list[tuple[str, str]]:
    """(locator, statement) with macros and register-addressed forms expanded.

    Same expansion as qasm_expanded_operations(), but it hands back statement
    TEXT rather than parsed operands, because table 5 detects parity checks by
    pattern-matching `cx a,b` and `measure a[0] -> syn[0]` directly. A
    whole-register `measure a -> syn` becomes one statement per qubit/bit pair,
    which is what makes an ancilla's syndrome bit visible.

    All expansions keep the locator of the statement they came from, so several
    expanded statements can share one locator -- that is correct: they came
    from one line of source.
    """
    expanded: list[tuple[str, str]] = []

    def emit(locator: str, statement: str) -> None:
        measure = QASM_MEASURE_FULL.match(statement)
        if measure is not None:
            qubit, bit = measure.group(1), measure.group(2)
            if "[" not in qubit and "[" not in bit and qubit in qregs and bit in cregs:
                for index in range(min(qregs[qubit], cregs[bit])):
                    expanded.append((locator, f"measure {qubit}[{index}] -> {bit}[{index}]"))
            else:
                expanded.append((locator, statement))
            return

        match = QASM_OPERATION.match(statement)
        if match is not None and match.group(1) in gate_definitions:
            parameters, body = gate_definitions[match.group(1)]
            substitution = dict(zip(parameters, qasm_operands(statement)))
            for body_statement in body:
                rewritten = body_statement
                for parameter, actual in substitution.items():
                    rewritten = re.sub(rf"\b{re.escape(parameter)}\b", actual, rewritten)
                emit(locator, rewritten)
            return

        if match is not None and match.group(1) == "barrier":
            return

        expanded.append((locator, statement))

    for locator, statement in statements:
        emit(locator, statement)
    return expanded


def qasm_known_gate_names(library_text: str, gate_definitions: dict) -> set[str]:
    """Gate names a circuit may legitimately call.

    Read from the archive's own `qelib1.inc` rather than hardcoded, plus the
    circuit's own definitions and the two OpenQASM builtins.
    """
    return (
        set(qasm_gate_definitions(library_text))
        | set(gate_definitions)
        | QASM_BUILTIN_GATES
        | {"measure", "reset"}
    )


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
        # Migrated to the shared tokenizer so table 5's locators agree with
        # tables 4 and 6. Previously this used Utility.get_flat_statements,
        # whose own statement counter numbered `if(syn==1) x q[0];` in
        # qec_sm_n5_transpiled.qasm as statement 13 while table 6 called the
        # same line 14. Detection logic below is unchanged.
        statements = qasm_statements(qasm_text)
        qregs, cregs = qasm_registers(statements)
        registers = {**qregs, **cregs}
        flat_statements = qasm_expanded_statements(
            statements, qregs, cregs, qasm_gate_definitions(qasm_text)
        )
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
                f"qasmbench:{member}#{check['measure_stmt_index']}"
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
                        "record_locator": stmt_idx,
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



    #----------------------------------Divith's Code (table 4) Starts here------------------------------------------------
    # Silver table 4: silver/qasmbench/circuit.parquet
    # One row = one parsed circuit variant. 6 rows: 3 benchmarks x {source, transpiled}.
    #
    # This is the table both stabilizer_check and conditional_correction point
    # at via circuit_id, so the identifier is built by the shared
    # qasm_circuit_identity() and nowhere else.

    CIRCUIT_RULES = {
        "qasm_circuit_qreg_declared":
            "circuit declares no quantum register",
        "qasm_circuit_measure_register_declared":
            "measure addresses a register that is not declared",
        "qasm_circuit_measure_width_matches":
            "register-addressed measure has mismatched qubit and bit widths",
        "qasm_circuit_operand_index_in_range":
            "operand index is outside its declared register size",
        "qasm_circuit_known_gate":
            "gate is neither defined in the circuit nor declared in qelib1.inc",
    }

    @staticmethod
    def build_circuit_rows(
        raw_bytes: bytes, benchmark: str, variant: str, member: str,
        archive_sha256: str, library_text: str,
    ):
        """Parses ONE circuit. Returns (circuit_rows, trace_rows, issue_rows)
        -- mirrors build_stabilizer_check_rows(...)."""
        circuit_id, benchmark_name, variant_name = qasm_circuit_identity(member)

        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            qasm_text = z.open(member).read().decode()

        statements = qasm_statements(qasm_text)
        qregs, cregs = qasm_registers(statements)
        gate_definitions = qasm_gate_definitions(qasm_text)
        known_gates = qasm_known_gate_names(library_text, gate_definitions)

        source_record_id = f"qasmbench:{member}"
        problems = []   # (rule_id, observed_value, severity)

        if not qregs:
            problems.append(("qasm_circuit_qreg_declared", member, "error"))

        # --- measure statements must address declared registers of equal width
        for _locator, statement in statements:
            measure = QASM_MEASURE_FULL.match(statement)
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

        operations = qasm_expanded_operations(statements, qregs, gate_definitions)

        # --- operand indices inside their declared register
        for _locator, name, operands in operations:
            for operand in operands:
                indexed = QASM_INDEXED_QUBIT.match(operand)
                if indexed is None:
                    continue
                register, index = indexed.group(1), int(indexed.group(2))
                if register in qregs and index >= qregs[register]:
                    problems.append(("qasm_circuit_operand_index_in_range", operand, "error"))

        # --- gate names, checked against the archive's own qelib1.inc
        for _locator, name, _operands in operations:
            if name not in known_gates:
                problems.append(("qasm_circuit_known_gate", name, "warning"))

        issue_rows = [
            {
                "issue_id": "issue_" + hashlib.md5(
                    f"{source_record_id}:{rule_id}:{observed}".encode()
                ).hexdigest()[:12],
                "source_record_id": source_record_id,
                "rule_id": rule_id,
                "severity": severity,
                "observed_value": str(observed),
                "action": "rejected" if severity == "error" else "accepted",
                "reason": Utility.CIRCUIT_RULES[rule_id],
            }
            for rule_id, observed, severity in problems
        ]

        if any(severity == "error" for _r, _o, severity in problems):
            return [], [], issue_rows

        # Declaration order, which is file order, so this string is repeatable.
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
        """Loops over every circuit found in the archive. Returns
        (circuit_df, trace_df, issues_df, candidate_count) -- mirrors
        qasmbench_stabilizer_check_table(raw_bytes)."""
        circuits = Utility.list_qasmbench_circuits(raw_bytes)
        archive = hashlib.sha256(raw_bytes).hexdigest()  # proof of the version

        # qelib1.inc is the standard gate library shipped in the archive. It is
        # NOT a circuit -- it is what the circuits `include`.
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            library_text = (
                z.read("qelib1.inc").decode() if "qelib1.inc" in z.namelist() else ""
            )

        all_circuit_rows = []
        all_trace_rows = []
        all_issue_rows = []

        for benchmark, variant, member in circuits:
            circuit_rows, trace_rows, issue_rows = Utility.build_circuit_rows(
                raw_bytes, benchmark, variant, member, archive, library_text
            )
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


    #----------------------------------Divith's Code (table 6) Starts here------------------------------------------------
    # Silver table 6: silver/qasmbench/conditional_correction.parquet
    # One row = one recovery operation controlled by a measured syndrome, i.e.
    # one `if(<creg> == <int>) <gate> <qubit>;` statement.
    #
    # Uses the shared qasm_* helpers above rather than its own parser, so the
    # source_record_id of a statement is the same here as anywhere else.

    CONDITIONAL_CORRECTION_RULES = {
        "qasm_cc_condition_register_declared":
            "condition register is not a declared creg",
        "qasm_cc_condition_value_in_range":
            "condition value does not fit the declared register width",
        "qasm_cc_single_target":
            "the contract stores exactly one target_qubit",
        "qasm_cc_target_is_indexed_qubit":
            "target is a whole register, not one indexed qubit",
        "qasm_cc_target_register_declared":
            "target register is not a declared qreg",
        "qasm_cc_target_index_in_range":
            "target index is outside the declared register size",
        "qasm_cc_condition_register_measured":
            "condition register is never written by a measure",
        "qasm_cc_condition_before_measure":
            "condition appears before the measure that writes its register",
    }

    @staticmethod
    def build_conditional_correction_rows(
        raw_bytes: bytes, benchmark: str, variant: str, member: str, archive_sha256: str
    ):
        """Parses ONE circuit. Returns (correction_rows, trace_rows, issue_rows)
        -- mirrors build_stabilizer_check_rows(...)."""
        circuit_id, _benchmark, _variant = qasm_circuit_identity(member)

        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            qasm_text = z.open(member).read().decode()

        statements = qasm_statements(qasm_text)
        qregs, cregs = qasm_registers(statements)

        # Execution order, so "was this register measured before the if?" can be
        # answered. Position in the list is the order; the locator is the ID.
        position = {locator: index for index, (locator, _s) in enumerate(statements)}
        measured_at = {}
        for locator, statement in statements:
            match = QASM_MEASURE.match(statement)
            if match is not None:
                measured_at.setdefault(match.group(2), position[locator])

        correction_rows = []  # storage for accepted correction rows
        trace_rows = []       # storage for trace table
        issue_rows = []       # storage for rejected corrections

        for locator, statement in statements:
            match = QASM_CONDITIONAL.match(statement)
            if match is None:
                continue

            register = match.group(1)
            value = int(match.group(2))
            operation = match.group(3).strip()
            source_record_id = f"qasmbench:{member}#{locator}"

            operation_match = QASM_OPERATION.match(operation)
            gate = operation_match.group(1) if operation_match else None
            targets = (
                [t.strip() for t in operation_match.group(3).split(",")]
                if operation_match
                else []
            )

            problems = []   # (rule_id, observed_value, severity)

            # --- condition side ---
            if register not in cregs:
                problems.append(("qasm_cc_condition_register_declared", register, "error"))
            elif not 0 <= value < 2 ** cregs[register]:
                problems.append(("qasm_cc_condition_value_in_range", value, "error"))

            # --- target side ---
            if len(targets) != 1:
                problems.append(("qasm_cc_single_target", operation, "error"))
            else:
                target_match = QASM_INDEXED_QUBIT.match(targets[0])
                if target_match is None:
                    problems.append(("qasm_cc_target_is_indexed_qubit", targets[0], "error"))
                elif target_match.group(1) not in qregs:
                    problems.append(("qasm_cc_target_register_declared", targets[0], "error"))
                elif int(target_match.group(2)) >= qregs[target_match.group(1)]:
                    problems.append(("qasm_cc_target_index_in_range", targets[0], "error"))

            # --- semantic warnings: a correction should react to a MEASURED
            # syndrome. These are accepted, not rejected -- the row is still a
            # faithful reading of the source.
            measured = measured_at.get(register)
            if register in cregs and measured is None:
                problems.append(("qasm_cc_condition_register_measured", register, "warning"))
            elif measured is not None and measured > position[locator]:
                problems.append((
                    "qasm_cc_condition_before_measure",
                    f"measure at {measured}, condition at {position[locator]}",
                    "warning",
                ))

            rejected = any(severity == "error" for _rule, _observed, severity in problems)

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
                        "reason": Utility.CONDITIONAL_CORRECTION_RULES[rule_id],
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

            # One statement, one Bronze member -> one trace row. Unlike a Google
            # shot, nothing here is assembled from several companion files.
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
        """Loops over every circuit found in the archive. Returns
        (correction_df, trace_df, issues_df, candidate_count) -- mirrors
        qasmbench_stabilizer_check_table(raw_bytes)."""
        circuits = Utility.list_qasmbench_circuits(raw_bytes)
        archive = hashlib.sha256(raw_bytes).hexdigest()  # proof of the version

        all_correction_rows = []
        all_trace_rows = []
        all_issue_rows = []

        for benchmark, variant, member in circuits:
            correction_rows, trace_rows, issue_rows = (
                Utility.build_conditional_correction_rows(
                    raw_bytes, benchmark, variant, member, archive
                )
            )
            all_correction_rows.extend(correction_rows)
            all_trace_rows.extend(trace_rows)
            all_issue_rows.extend(issue_rows)

        # Candidates = statements that looked like a conditional correction,
        # accepted or not. Reconciles against a plain text scan for "if(".
        candidate_count = len(all_correction_rows) + len(
            {row["source_record_id"] for row in all_issue_rows if row["action"] == "rejected"}
        )

        correction_df = pd.DataFrame(
            all_correction_rows,
            columns=["source_record_id", "circuit_id", "condition_register",
                     "condition_value", "gate", "target_qubit"],
        ).astype(
            {
                "source_record_id": "string",
                "circuit_id": "string",
                "condition_register": "string",
                "condition_value": "int64",
                "gate": "string",
                "target_qubit": "string",
            }
        ).sort_values(["circuit_id", "condition_value"]).reset_index(drop=True)

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

        return correction_df, trace_df, issues_df, candidate_count



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


    # --- table 4 wiring: circuit ---
    df_circuit, df_trace5, df_issues5, circuit_candidates = (
        Utility.qasmbench_circuit_table(qasmbench_raw_bytes)
    )
    df_circuit.to_parquet("silver/qasmbench/circuit.parquet", index=False)

    df_trace = pd.concat([df_trace, df_trace5], axis=0, ignore_index=True)
    df_issues = pd.concat([df_issues, df_issues5], axis=0, ignore_index=True)

    row_counts["circuit"] = {
        "read": circuit_candidates,
        "accepted": len(df_circuit),
        "rejected": circuit_candidates - len(df_circuit),
    }
    upload_map["silver/qasmbench/circuit.parquet"] = "silver/qasmbench/circuit.parquet"

    result.input_count += circuit_candidates
    result.output_count += len(df_circuit)
    result.issue_count += circuit_candidates - len(df_circuit)
    # --- end table 4 wiring ---
    # --- table 6 wiring: conditional_correction ---
    df_correction, df_trace4, df_issues4, correction_candidates = (
        Utility.qasmbench_conditional_correction_table(qasmbench_raw_bytes)
    )
    df_correction.to_parquet("silver/qasmbench/conditional_correction.parquet", index=False)

    df_trace = pd.concat([df_trace, df_trace4], axis=0, ignore_index=True)
    df_issues = pd.concat([df_issues, df_issues4], axis=0, ignore_index=True)

    row_counts["conditional_correction"] = {
        "read": correction_candidates,
        "accepted": len(df_correction),
        "rejected": correction_candidates - len(df_correction),
    }
    upload_map["silver/qasmbench/conditional_correction.parquet"] = "silver/qasmbench/conditional_correction.parquet"

    result.input_count += correction_candidates
    result.output_count += len(df_correction)
    result.issue_count += correction_candidates - len(df_correction)
    # --- end table 6 wiring ---
 
    df_issues["run_id"] = run_id
    df_issues = df_issues[["issue_id", "run_id", "source_record_id", "rule_id",
                           "severity", "observed_value", "action", "reason"]]

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