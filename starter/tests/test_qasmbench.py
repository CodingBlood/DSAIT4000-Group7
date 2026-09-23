"""Tests for the shared OpenQASM parsing and Silver table 6.

Every test builds a tiny in-memory archive instead of reading Bronze, per
faq.md: "Use tiny representative inputs for individual checks." Nothing here
touches MinIO, so the suite runs offline and fast.

Most of the validation rules never fire on the supplied release -- all six real
corrections are clean. A rule that never fires still has to be proven to work,
so each one gets a crafted input that must trip it.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from quantum_lake_student.stages.prepare_data import (
    Utility,
    qasm_circuit_identity,
    qasm_condition_trigger_bits,
    qasm_gate_definitions,
    qasm_registers,
    qasm_statements,
)

MEMBER = "small/demo_n5/demo_n5.qasm"
FAKE_SHA = "0" * 64

PREAMBLE = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
qreg a[2];
creg c[3];
creg syn[2];
measure a -> syn;
"""


def archive_of(qasm: str, member: str = MEMBER) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr(member, qasm)
    return buffer.getvalue()


def corrections(qasm: str, member: str = MEMBER):
    """(rows, trace_rows, issue_rows) for a one-circuit archive."""
    return Utility.build_conditional_correction_rows(
        archive_of(qasm, member), "demo_n5", "source", member, FAKE_SHA
    )


def rules_fired(issue_rows) -> set[str]:
    return {row["rule_id"] for row in issue_rows}


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------

def test_valid_correction_is_accepted_with_contract_columns():
    rows, traces, issues = corrections(PREAMBLE + "if(syn==1) x q[0];\n")
    assert len(rows) == 1
    assert issues == []
    assert rows[0] == {
        "source_record_id": f"qasmbench:{MEMBER}#L8.0",
        "circuit_id": "qasmbench:demo_n5:source",
        "condition_register": "syn",
        "condition_value": 1,
        "gate": "x",
        "target_qubit": "q[0]",
    }
    assert len(traces) == 1
    assert traces[0]["record_locator"] == "L8.0"
    assert traces[0]["input_sha256"] == FAKE_SHA


def test_gate_name_is_read_not_assumed():
    """Every correction in the release is `x`. The parser must not know that."""
    rows, _t, _i = corrections(PREAMBLE + "if(syn==2) z q[1];\n")
    assert rows[0]["gate"] == "z"


def test_circuits_without_conditions_produce_no_rows():
    rows, traces, issues = corrections(PREAMBLE + "x q[0];\nbarrier q;\n")
    assert (rows, traces, issues) == ([], [], [])


# --------------------------------------------------------------------------
# rejection rules -- one crafted failure each
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "statement, expected_rule",
    [
        ("if(syn==4) x q[0];", "qasm_cc_condition_value_in_range"),
        ("if(nope==1) x q[0];", "qasm_cc_condition_register_declared"),
        ("if(syn==1) cx q[0],q[1];", "qasm_cc_single_target"),
        ("if(syn==1) x q;", "qasm_cc_target_is_indexed_qubit"),
        ("if(syn==1) x z[0];", "qasm_cc_target_register_declared"),
        ("if(syn==1) x q[7];", "qasm_cc_target_index_in_range"),
    ],
)
def test_invalid_correction_is_rejected_with_a_reason(statement, expected_rule):
    rows, traces, issues = corrections(PREAMBLE + statement + "\n")

    assert rows == [], "an invalid record must not reach Silver"
    assert traces == [], "a rejected record must not claim a trace row"
    assert expected_rule in rules_fired(issues)

    issue = next(row for row in issues if row["rule_id"] == expected_rule)
    assert issue["severity"] == "error"
    assert issue["action"] == "rejected"
    assert issue["observed_value"], "the original value must be preserved"
    assert issue["reason"], "the reason must be human-readable"


def test_condition_value_upper_bound_is_inclusive_of_the_register_width():
    """creg syn[2] holds 0..3. 3 is valid, 4 is not."""
    ok, _t, ok_issues = corrections(PREAMBLE + "if(syn==3) x q[1];\n")
    assert len(ok) == 1 and ok_issues == []

    bad, _t, bad_issues = corrections(PREAMBLE + "if(syn==4) x q[1];\n")
    assert bad == []
    assert "qasm_cc_condition_value_in_range" in rules_fired(bad_issues)


# --------------------------------------------------------------------------
# warnings -- recorded, but the row is still faithful to the source
# --------------------------------------------------------------------------

def test_condition_before_its_measure_warns_but_is_accepted():
    qasm = """OPENQASM 2.0;
qreg q[3];
qreg a[2];
creg syn[2];
if(syn==1) x q[0];
measure a -> syn;
"""
    rows, _t, issues = corrections(qasm)
    assert len(rows) == 1, "a warning must not drop the row"
    issue = next(r for r in issues if r["rule_id"] == "qasm_cc_condition_before_measure")
    assert issue["severity"] == "warning"
    assert issue["action"] == "accepted"


def test_never_measured_condition_register_warns():
    qasm = "OPENQASM 2.0;\nqreg q[3];\ncreg syn[2];\nif(syn==1) x q[0];\n"
    rows, _t, issues = corrections(qasm)
    assert len(rows) == 1
    assert "qasm_cc_condition_register_measured" in rules_fired(issues)


# --------------------------------------------------------------------------
# the shared tokenizer -- the three documented parsing traps
# --------------------------------------------------------------------------

def test_gate_definition_body_is_not_executed_code():
    """`gate syndrome {...}` is a definition. Only its call site executes."""
    qasm = """OPENQASM 2.0;
qreg q[3];
qreg a[2];
gate syndrome d1,d2,a1
{
  cx d1,a1; cx d2,a1;
}
syndrome q[0],q[1],a[0];
"""
    texts = [statement for _locator, statement in qasm_statements(qasm)]
    assert "cx d1,a1" not in texts
    assert "syndrome q[0],q[1],a[0]" in texts

    definitions = qasm_gate_definitions(qasm)
    assert definitions["syndrome"] == (["d1", "d2", "a1"], ["cx d1,a1", "cx d2,a1"])


def test_line_numbers_survive_a_gate_definition():
    """Dropping the gate block must not shift later line numbers."""
    qasm = """OPENQASM 2.0;
qreg q[3];
gate g a1,a2
{
  cx a1,a2;
}
x q[0];
"""
    locators = dict(map(reversed, qasm_statements(qasm)))
    assert locators["x q[0]"] == "L7.0", "x q[0] really is on line 7"


def test_several_statements_on_one_line_get_distinct_locators():
    qasm = "OPENQASM 2.0;\nqreg q[3];\nqreg a[2];\ncx q[0],a[0]; cx q[1],a[0];\n"
    found = [loc for loc, stmt in qasm_statements(qasm) if stmt.startswith("cx")]
    assert found == ["L4.0", "L4.1"]
    assert len(set(found)) == 2, "locators must stay unique within a line"


def test_comments_are_stripped_without_moving_lines():
    qasm = "OPENQASM 2.0;\n// just a comment\nqreg q[3]; // trailing\nx q[0];\n"
    locators = dict(map(reversed, qasm_statements(qasm)))
    assert locators["qreg q[3]"] == "L3.0"
    assert locators["x q[0]"] == "L4.0"


def test_registers_are_read_per_register_not_globally():
    """q[0] and a[0] are different qubits; sizes are tracked per register."""
    qregs, cregs = qasm_registers(qasm_statements(PREAMBLE))
    assert qregs == {"q": 3, "a": 2}
    assert cregs == {"c": 3, "syn": 2}


def test_circuit_identity_distinguishes_variants():
    source = "small/qec_sm_n5/qec_sm_n5.qasm"
    transpiled = "small/qec_sm_n5/qec_sm_n5_transpiled.qasm"
    assert qasm_circuit_identity(source) == ("qasmbench:qec_sm_n5:source", "qec_sm_n5", "source")
    assert qasm_circuit_identity(transpiled)[0] == "qasmbench:qec_sm_n5:transpiled"
    assert qasm_circuit_identity(source)[0] != qasm_circuit_identity(transpiled)[0]


# --------------------------------------------------------------------------
# Gold helper
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [(0, []), (1, ["syn[0]"]), (2, ["syn[1]"]), (3, ["syn[0]", "syn[1]"])],
)
def test_condition_value_maps_to_the_syndrome_bits_that_fired(value, expected):
    assert qasm_condition_trigger_bits("syn", value, 2) == expected


# --------------------------------------------------------------------------
# repeatability -- brief.md: "must leave stable identifiers"
# --------------------------------------------------------------------------

def test_second_parse_of_unchanged_bytes_is_identical():
    qasm = PREAMBLE + "if(syn==1) x q[0];\nif(syn==2) x q[2];\n"
    first = corrections(qasm)
    second = corrections(qasm)
    assert first == second


def test_identical_statements_get_different_identifiers():
    """Two textually identical lines are two separate recovery operations."""
    qasm = PREAMBLE + "if(syn==1) x q[0];\nif(syn==1) x q[0];\n"
    rows, _t, _i = corrections(qasm)
    assert len(rows) == 2
    assert rows[0]["source_record_id"] != rows[1]["source_record_id"]


def test_identifier_does_not_depend_on_the_archive_it_arrives_in():
    """The id is a function of member + position, not of packaging."""
    qasm = PREAMBLE + "if(syn==1) x q[0];\n"
    rows_a, _t, _i = corrections(qasm)
    raw = archive_of("// filler\n", "small/other/other.qasm")
    buffer = io.BytesIO(raw)
    with zipfile.ZipFile(buffer, "a") as handle:
        handle.writestr(MEMBER, qasm)
    rows_b, _t, _i = Utility.build_conditional_correction_rows(
        buffer.getvalue(), "demo_n5", "source", MEMBER, FAKE_SHA
    )
    assert rows_a[0]["source_record_id"] == rows_b[0]["source_record_id"]
