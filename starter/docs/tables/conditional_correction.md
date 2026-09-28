# conditional_correction

**Path:** silver/qasmbench/conditional_correction.parquet
**Owner:** Divith Phogat
**Status:** Finished

## What one row represents

One recovery operation controlled by a measured syndrome — that is, one
`if(<creg> == <int>) <gate> <qubit>;` statement in a QASM circuit.

In plain terms: *"when the damage signature reads N, this circuit repairs that
qubit."*

**6 rows.** All from `qec_sm_n5` — 3 in the source variant, 3 in transpiled.
The other two benchmarks (`error_correctiond3_n5`, `qec_en_n5`) contain no
conditional statements at all. A whole-archive text scan finds `if(` on exactly
6 lines, which is the reconciliation target.

## Where each column comes from

| Column | Source | Notes |
|---|---|---|
| `source_record_id` | archive member + line locator | `qasmbench:<member>#L<line>.<ordinal>` |
| `circuit_id` | member path | `qasmbench:<benchmark>:<variant>` — **FK into table 4 (`circuit`)** |
| `condition_register` | `if(` **syn** `==1)` | always a whole creg; OpenQASM 2.0 has no bit-level condition |
| `condition_value` | `if(syn==` **1** `)` | int64; must fit the declared register width |
| `gate` | `if(syn==1)` **x** `q[0]` | read from the file, never assumed |
| `target_qubit` | `if(syn==1) x` **q[0]** | keeps the register name — `q[0]` ≠ `a[0]` |

## Our understanding of this table

`qec_sm_n5` is a 3-qubit repetition code with two parity checks:

```
a[0] checks q[0] vs q[1]  ->  syn[0]  (worth 1)
a[1] checks q[1] vs q[2]  ->  syn[1]  (worth 2)
```

| `syn` | checks fired | broken qubit | repair |
|---|---|---|---|
| 0 | neither | nothing | *no line — correct* |
| 1 | check A only | `q[0]` (only in A) | `x q[0]` |
| 2 | check B only | `q[2]` (only in B) | `x q[2]` |
| 3 | both | `q[1]` (in both) | `x q[1]` |

`notebooks/t6_discovery.ipynb` §9 derives this independently from the circuit's
`cx`/`measure` statements and confirms the file agrees on all three — so the
rows are semantically correct, not merely parsed.

**This table plus table 5 answers Part I analysis question 3** ("how does the
repetition-code circuit map data qubits to parity-check ancillas, syndrome bits,
and conditional corrections?") as a multi-table join, which also satisfies the
"at least one query joins three or more Gold tables" requirement.

## Decisions made

- **Locator format `#L<line>.<ordinal>`** rather than a statement index. A line
  number belongs to the file, not to a parser, so it cannot drift. See
  `notes/qasmbench-silver.md` for the full reasoning and the bug that prompted
  it.
- **Source and transpiled are NOT deduplicated.** They carry identical
  corrections at different positions, but they are two separate circuits — table
  4 has an explicit `variant` column for exactly this.
- **`syn==0` gets no row.** No check fired means nothing to repair. That is the
  circuit being correct, not a missing record.
- **Silver keeps exactly the six contract columns.** The derived "which syndrome
  bits fired" mapping is available via `qasm_condition_trigger_bits()` for Gold,
  where there are no schema restrictions.
- **`gate` is read, not hardcoded.** It is always `x` in this release; the
  parser does not know that.

## Open questions

- ~~Table 5 has not migrated to the shared tokenizer~~ — **resolved**, all
  three QASMBench tables now share `qasm_statements()`.
- ~~Table 4 (`circuit`) has no owner and no code~~ — **resolved**, built and in
  MinIO; FK integrity verified both ways.

## Validation checks implemented

Rejected (severity `error`, excluded from Silver, written to `data_issues`):

- [x] `qasm_cc_condition_register_declared` — condition register is a declared `creg`
- [x] `qasm_cc_condition_value_in_range` — `0 <= value < 2**width`
- [x] `qasm_cc_single_target` — exactly one `target_qubit`
- [x] `qasm_cc_target_is_indexed_qubit` — not a whole register
- [x] `qasm_cc_target_register_declared` — target register is a declared `qreg`
- [x] `qasm_cc_target_index_in_range` — index inside the declared size

Accepted with a warning:

- [x] `qasm_cc_condition_register_measured` — register is written by a `measure`
- [x] `qasm_cc_condition_before_measure` — the `if` follows that measure

**All rules pass on this release: 6 read, 6 accepted, 0 rejected, 0 issues.**
Each rule has a crafted failing input in `tests/test_qasmbench.py` (25 tests), so
a rule that never fires on real data is still proven to work.

Also tested: gate-definition bodies excluded, line numbers preserved across a
gate block, multiple statements per line get distinct locators, and a second
parse of unchanged bytes is identical.
