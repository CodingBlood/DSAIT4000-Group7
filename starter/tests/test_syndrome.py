"""Tests for Silver table 1 -- qec_syndromes/syndrome_observation.

Every test builds a tiny in-memory zip rather than reading Bronze, so the suite
runs offline. The real release is clean (75,598 rows, 70,000,000 weighted, zero
rejects), which means none of the rejection rules ever fire on real data -- so
each one gets a crafted input that must trip it, otherwise the rule is unproven.

The two checks worth the most attention are bit order and the weight, because
both fail silently: a check-major flatten produces a perfectly valid 16-byte
array, and expanding `quantity` produces a perfectly valid table that answers a
different question.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from quantum_lake_student.stages.prepare_data import SyndromeUtility

ARCHIVE = "bronze/source=qec_syndromes/syndromes_dataset.zip"
MEMBER = "d-3_pfr-0.001000_nb-10M.csv"
HEADER = "labels,syndromes,quantity"
ZERO = "((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))"


def archive_of(csv_text: str, member: str = MEMBER) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr(member, csv_text)
    return buffer.getvalue()


def csv_of(*data_lines: str) -> str:
    return "\n".join([HEADER, *data_lines]) + "\n"


def build(csv_text: str, member: str = MEMBER):
    """(rows, issue_rows, trace_rows) for a one-file archive."""
    return SyndromeUtility.build_rows(
        archive_of(csv_text, member), member, ARCHIVE, "test-run"
    )


def reasons(issue_rows) -> str:
    return " ".join(row["reason"] for row in issue_rows)


# --------------------------------------------------------------------------
# the happy path and the contract
# --------------------------------------------------------------------------

def test_valid_row_is_accepted_with_contract_columns():
    rows, issues, traces = build(csv_of(f'0,"{ZERO}",1234'))
    assert len(rows) == 1
    assert not [i for i in issues if i["action"] == "rejected"]

    row = rows[0]
    assert row["physical_fault_rate"] == 0.001
    assert row["round_count"] == 4
    assert row["check_count"] == 4
    assert row["logical_error_label"] is False
    assert row["quantity"] == 1234
    assert len(row["syndrome_bits"]) == 16
    assert isinstance(row["syndrome_bits"], bytes)

    assert len(traces) == 1
    assert traces[0]["source_name"] == "qec_syndromes"
    assert traces[0]["archive_member"] == MEMBER


def test_physical_fault_rate_comes_from_the_filename_not_a_column():
    """The CSV has no fault-rate column; it is encoded in the member name."""
    assert "pfr" not in HEADER
    rows, _i, _t = build(csv_of(f'0,"{ZERO}",1'), "d-3_pfr-0.005000_nb-10M.csv")
    assert rows[0]["physical_fault_rate"] == 0.005


def test_label_one_becomes_true():
    rows, _i, _t = build(csv_of(f'1,"{ZERO}",7'))
    assert rows[0]["logical_error_label"] is True


# --------------------------------------------------------------------------
# bit order -- the silent-corruption trap
# --------------------------------------------------------------------------

def test_syndrome_bits_are_flattened_round_major():
    """silver-tables.md: "round first and check second".

    Check 2 fires in round 0 only. Round-major puts that bit at index 2.
    Check-major would put it at index 8 -- an equally valid 16-byte array that
    is silently scrambled, which is why this is a named required check.
    """
    one_bit = "((0, 0, 1, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))"
    rows, _i, _t = build(csv_of(f'0,"{one_bit}",1'))
    bits = rows[0]["syndrome_bits"]

    assert bits[2] == 1, "round-major: round 0, check 2 lands at index 2"
    assert bits[8] == 0, "index 8 set would mean a check-major flatten"
    assert sum(bits) == 1


def test_round_order_is_preserved_across_rounds():
    """Check 0 in round 3 only -> index 3*4 + 0 = 12."""
    late = "((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (1, 0, 0, 0))"
    rows, _i, _t = build(csv_of(f'0,"{late}",1'))
    bits = rows[0]["syndrome_bits"]
    assert bits[12] == 1
    assert sum(bits) == 1


def test_every_accepted_row_has_exactly_sixteen_bytes():
    full = "((1, 1, 1, 1), (1, 1, 1, 1), (1, 1, 1, 1), (1, 1, 1, 1))"
    rows, _i, _t = build(csv_of(f'0,"{ZERO}",1', f'1,"{full}",2'))
    assert all(len(r["syndrome_bits"]) == 16 for r in rows)
    assert sum(rows[1]["syndrome_bits"]) == 16


# --------------------------------------------------------------------------
# rejection rules -- one crafted failure each
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "syndromes, note",
    [
        ("((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))", "only three rounds"),
        ("((0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))", "five rounds"),
        ("((0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))", "three checks in round 0"),
        ("((0, 0, 2, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0))", "value 2 is not binary"),
        ("not a tuple at all", "unparseable"),
    ],
)
def test_malformed_syndrome_is_rejected_and_recorded(syndromes, note):
    rows, issues, traces = build(csv_of(f'0,"{syndromes}",1'))

    assert rows == [], f"{note}: must not reach Silver"
    assert traces == [], f"{note}: a rejected row must not claim a trace row"

    rejected = [i for i in issues if i["action"] == "rejected"]
    assert len(rejected) == 1, note
    issue = rejected[0]
    assert issue["severity"] == "error"
    assert issue["run_id"] == "test-run"
    assert issue["observed_value"], "the original value must be preserved"
    assert issue["reason"], "the reason must be human-readable"


@pytest.mark.parametrize("quantity", ["0", "-5"])
def test_non_positive_quantity_is_rejected(quantity):
    rows, issues, _t = build(csv_of(f'0,"{ZERO}",{quantity}'))
    assert rows == []
    assert "quantity" in reasons(issues)


def test_rejected_and_accepted_rows_reconcile():
    """rubric.md: "accepted and rejected counts reconcile"."""
    bad = "((0, 0, 0, 0), (0, 0, 0, 0))"
    text = csv_of(f'0,"{ZERO}",10', f'1,"{bad}",20', f'0,"{ZERO}",30')
    rows, issues, _t = build(text)
    rejected = [i for i in issues if i["action"] == "rejected"]
    assert len(rows) + len(rejected) == 3


# --------------------------------------------------------------------------
# the aggregate grain -- quantity is a weight, not a row multiplier
# --------------------------------------------------------------------------

def test_quantity_is_a_weight_and_is_never_expanded():
    """faq.md: expanding aggregates to 70 million rows adds no information."""
    rows, _i, _t = build(csv_of(f'0,"{ZERO}",9987291'))
    assert len(rows) == 1, "one CSV row stays one Silver row"
    assert rows[0]["quantity"] == 9987291


def test_same_syndrome_under_both_labels_is_kept_twice():
    """quantum-data-primer.md: the same syndrome may validly occur with both
    labels, so syndrome alone is not a key and must not be deduplicated."""
    rows, issues, _t = build(csv_of(f'0,"{ZERO}",121002', f'1,"{ZERO}",99'))

    assert len(rows) == 2, "identical syndromes, different labels: both are real"
    assert {r["logical_error_label"] for r in rows} == {False, True}
    assert {r["quantity"] for r in rows} == {121002, 99}
    assert rows[0]["source_record_id"] != rows[1]["source_record_id"]
    assert not [i for i in issues if i["action"] == "rejected"]


def test_identical_rows_still_get_distinct_identifiers():
    """Position disambiguates, so a genuine repeat is not silently collapsed."""
    rows, _i, _t = build(csv_of(f'0,"{ZERO}",5', f'0,"{ZERO}",5'))
    assert len({r["source_record_id"] for r in rows}) == 2


# --------------------------------------------------------------------------
# the filename weight reconciliation
# --------------------------------------------------------------------------

def test_file_total_matching_the_filename_raises_no_issue():
    rows, issues, _t = build(csv_of(f'0,"{ZERO}",10000000'))
    assert len(rows) == 1
    assert issues == []


def test_file_total_short_of_the_filename_is_flagged():
    """brief.md requires a check for "ten million weighted observations per
    fault-rate file". A shortfall is a finding, not a rejected row."""
    rows, issues, _t = build(csv_of(f'0,"{ZERO}",42'))
    assert len(rows) == 1, "the row itself is valid"
    flagged = [i for i in issues if i["rule_id"] == "syndrome_file_total_quantity"]
    assert len(flagged) == 1
    assert flagged[0]["severity"] == "warning"
    assert flagged[0]["observed_value"] == "42"
    assert "10000000" in flagged[0]["reason"]


# --------------------------------------------------------------------------
# filenames and identifiers
# --------------------------------------------------------------------------

def test_filename_is_parsed_into_distance_rate_and_nominal_count():
    parsed = SyndromeUtility.parse_filename("d-3_pfr-0.000500_nb-10M.csv")
    assert parsed == {
        "distance": 3,
        "physical_fault_rate": 0.0005,
        "fault_rate_text": "0.000500",
        "nb": "10M",
    }


def test_unparseable_filename_is_rejected():
    assert SyndromeUtility.parse_filename("README.txt") is None
    with pytest.raises(ValueError):
        build(csv_of(f'0,"{ZERO}",1'), "not-a-syndrome-file.csv")


def test_only_csv_members_are_listed():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr(MEMBER, csv_of(f'0,"{ZERO}",1'))
        handle.writestr("README.txt", "documentation, not data")
    assert SyndromeUtility.list_csv_members(buffer.getvalue()) == [MEMBER]


# --------------------------------------------------------------------------
# repeatability -- brief.md: "must leave stable identifiers"
# --------------------------------------------------------------------------

def test_second_build_of_unchanged_bytes_is_identical():
    text = csv_of(f'0,"{ZERO}",10', f'1,"{ZERO}",20')
    assert build(text) == build(text)


def test_identifier_does_not_depend_on_other_members_in_the_archive():
    text = csv_of(f'0,"{ZERO}",10')
    alone, _i, _t = build(text)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("README.txt", "unrelated")
        handle.writestr(MEMBER, text)
    with_sibling, _i, _t = SyndromeUtility.build_rows(
        buffer.getvalue(), MEMBER, ARCHIVE, "test-run"
    )
    assert alone[0]["source_record_id"] == with_sibling[0]["source_record_id"]


# --------------------------------------------------------------------------
# the documented-header check (brief.md minimum check)
# --------------------------------------------------------------------------

README = """The file format is a csv file with the following columns:
- label: binary label (0: no error, 1: error)
- syndromes: syndrome measurement sequence
- quantity: number of samples for this label + syndrome sequence
"""


def archive_with_readme(csv_text: str, readme: str = README) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("README.txt", readme)
        handle.writestr(MEMBER, csv_text)
    return buffer.getvalue()


def test_documented_columns_are_read_from_the_readme():
    assert SyndromeUtility.read_documented_columns(
        archive_with_readme(csv_of(f'0,"{ZERO}",1'))
    ) == ["label", "syndromes", "quantity"]


def header_issues_for(raw: bytes) -> list[dict]:
    """syndrome_table returns DataFrames, so pull the header rule back out."""
    _df, issues, _trace, _read = SyndromeUtility.syndrome_table(raw, ARCHIVE, "test-run")
    if issues.empty:
        return []
    matched = issues[issues["rule_id"] == "syndrome_documented_header_matches"]
    return matched.to_dict("records")


def test_documented_label_versus_actual_labels_is_recorded():
    """README.txt says `label`; every CSV header says `labels`. The data is
    usable either way, so the mismatch is a warning that stays visible."""
    found = header_issues_for(archive_with_readme(csv_of(f'0,"{ZERO}",10000000')))

    assert len(found) == 1, "recorded once per archive, not once per CSV"
    issue = found[0]
    assert issue["severity"] == "warning"
    assert issue["action"] == "accepted"
    assert issue["run_id"] == "test-run"
    assert "label" in issue["observed_value"] and "labels" in issue["observed_value"]


def test_matching_header_raises_no_issue():
    readme = README.replace("- label:", "- labels:")
    assert header_issues_for(
        archive_with_readme(csv_of(f'0,"{ZERO}",10000000'), readme)
    ) == []


def test_archive_without_a_readme_is_not_an_issue():
    assert header_issues_for(archive_of(csv_of(f'0,"{ZERO}",10000000'))) == []


# --------------------------------------------------------------------------
# experiment_id readability
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "member, expected",
    [
        ("d-3_pfr-0.000010_nb-10M.csv", "syndrome_d3_pfr0.000010"),
        ("d-3_pfr-0.000050_nb-10M.csv", "syndrome_d3_pfr0.000050"),
        ("d-3_pfr-0.005000_nb-10M.csv", "syndrome_d3_pfr0.005000"),
    ],
)
def test_experiment_id_spells_the_rate_as_the_filename_does(member, expected):
    """Formatting the parsed float instead would give "syndrome_d3_pfr1e-05"
    for the smallest rate -- deterministic, but scientific notation inside an
    identifier is a trap for anyone joining on it."""
    rows, _i, _t = build(csv_of(f'0,"{ZERO}",1'), member)
    assert rows[0]["experiment_id"] == expected
    assert "e-0" not in rows[0]["experiment_id"]


def test_physical_fault_rate_stays_a_float_even_though_the_id_is_text():
    rows, _i, _t = build(csv_of(f'0,"{ZERO}",1'), "d-3_pfr-0.000010_nb-10M.csv")
    assert rows[0]["physical_fault_rate"] == 1e-05
    assert isinstance(rows[0]["physical_fault_rate"], float)
