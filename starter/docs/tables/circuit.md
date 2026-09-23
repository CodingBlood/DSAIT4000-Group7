# circuit

**Path:** silver/qasmbench/circuit.parquet
**Owner:** Divith Phogat
**Status:** Finished

## What one row represents

One parsed circuit variant — one `.qasm` file in the archive.

**6 rows** = 3 benchmarks x 2 variants (`source`, `transpiled`). Both variants
get a row: table 4 has an explicit `variant` column, and transpilation produces
a genuinely different circuit even though it computes the same thing.

`qelib1.inc` is **not** a circuit. It is the standard gate library the circuits
`include`, and this table reads it to check gate names.

## Where each column comes from

| Column | Source | Notes |
|---|---|---|
| `source_record_id` | archive member | `qasmbench:<member>` — grain is the whole file, so no line locator |
| `circuit_id` | member path | `qasmbench:<benchmark>:<variant>` — **FK target for tables 5 and 6** |
| `benchmark_name` | directory name | e.g. `qec_sm_n5` |
| `variant` | filename suffix | `transpiled` if it ends `_transpiled.qasm`, else `source` |
| `register_declarations` | `qreg`/`creg` lines | declaration order = file order, so it is repeatable |
| `qubit_count` | sum of `qreg` sizes | **sum**, not the first register: `q[3]` + `a[2]` = 5 |
| `measurement_count` | expanded `measure` ops | `measure a -> syn` is 2, not 1 |
| `two_qubit_gate_count` | expanded 2-operand ops | a macro call expands to its body |

## Verified values

| circuit | registers | qubits | measurements | 2q gates |
|---|---|---|---|---|
| `error_correctiond3_n5` source | `q[5]`, `c[5]` | 5 | 5 | 49 |
| `error_correctiond3_n5` transpiled | `q[5]`, `c[5]` | 5 | 5 | 49 |
| `qec_en_n5` source | `q[5]`, `c[5]` | 5 | 5 | 10 |
| `qec_en_n5` transpiled | `q[5]`, `c[5]` | 5 | 5 | 10 |
| `qec_sm_n5` source | `q[3]`, `a[2]`, `c[3]`, `syn[2]` | 5 | 5 | 4 |
| `qec_sm_n5` transpiled | `q[3]`, `a[2]`, `c[3]`, `syn[2]` | 5 | 5 | 4 |

**Source and transpiled agree on every count, for all three benchmarks.** That
is a free correctness check: transpilation changes how gates are expressed, not
how many measurements or entangling operations the circuit performs. A parser
that returns different numbers for the two variants of one benchmark is wrong.

## Our understanding of this table

Three counting rules, all from `silver-tables.md` and `data-sources.md`:

1. **A gate definition body is not executed code.** In `qec_sm_n5.qasm`,
   `gate syndrome {...}` contains four `cx`. Those are a *definition*. The
   single line `syndrome q[0],q[1],q[2],a[0],a[1];` is the execution, and it
   contributes 4 two-qubit operations by expanding the body once. Calling it
   twice would contribute 8.
2. **Register-addressed operations expand.** `measure a -> syn` is 2
   measurements because `qreg a[2]` holds 2 qubits.
3. **`barrier` is not an operation.** It is a compiler fence.

Library gates are **not** expanded: a `cx` is one two-qubit operation, not its
`U`/`CX` decomposition. Only gates defined inside the circuit are macros.

## Decisions made

- **Two-qubit-ness is derived from operand count**, not a hardcoded gate list.
  An operation with exactly two qubit operands is a two-qubit operation. This
  was cross-checked against an independent implementation using a hardcoded
  list (`cx`, `cz`, `swap`, ...) and both give 49 / 10 / 4.
- **Gate names are validated against the archive's own `qelib1.inc`** rather
  than a list we maintain. Unknown gates warn rather than reject — an
  unrecognised name is more likely our gap than a broken circuit.
- **`source_record_id` has no line locator.** The grain is the whole file, so
  the member path alone identifies the record. Tables 5 and 6 need statement
  locators because their grain is a statement.
- **Both variants are kept.** Not deduplicated.

## Open questions

- None outstanding. All three QASMBench tables share one tokenizer.

## Validation checks implemented

Rejected (severity `error`):

- [x] `qasm_circuit_qreg_declared` — at least one quantum register
- [x] `qasm_circuit_measure_register_declared` — measure addresses declared registers
- [x] `qasm_circuit_measure_width_matches` — `measure q -> c` needs equal widths
- [x] `qasm_circuit_operand_index_in_range` — operand index inside its register

Accepted with a warning:

- [x] `qasm_circuit_known_gate` — gate is in `qelib1.inc` or defined locally

**All rules pass on this release: 6 read, 6 accepted, 0 rejected, 0 issues.**
13 tests in `tests/test_qasmbench.py` cover the counting rules and each failure
case, including one asserting that tables 4 and 6 derive `circuit_id`
identically.
