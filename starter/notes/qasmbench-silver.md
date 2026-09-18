# QASMBench Silver tables — notes

Covers Silver tables **4** (`circuit`) and **6** (`conditional_correction`).
Every number below was verified against the Bronze bytes, not copied from the
handout. Working notebook: `notebooks/discovery.ipynb`.

Bronze object: `bronze/source=qasmbench/qasmbench-qec.zip`
`input_sha256`: `60307f88e34b1f752b94223d6d136da72c629b4b625ac6d1c30e5e2e4f85722a`
Size: 144,172 bytes · 19 archive members · no unsafe member names

---

## What this source actually is

Three QEC circuits as **OpenQASM 2.0 text files**. No experiments, no shots,
no labels. QASMBench exists to make the parity-check mechanism explicit:
it shows *how* a syndrome is produced and *what gets done about it*.

**It does not join to `google_qec` or `qec_syndromes`.** No shared experiment
identifier exists. `error_correctiond3_n5` and the Google `d3` experiments both
say "distance 3" and are unrelated artifacts — that shared vocabulary is the
rejected join the brief requires us to document.

**Do not simulate the circuits** (`data-sources.md`) and **do not turn them into
training rows** (`faq.md` — they have no labels).

### The archive

| kind | members | use |
| --- | --- | --- |
| `.qasm` circuits | **6** | the only input to tables 4/5/6 |
| `.png` renders | 6 | ignore |
| `README.md` (root + per benchmark) | 4 | ignore |
| `LICENSE`, `NOTICE` | 2 | attribution only |
| `qelib1.inc` | 1 | **gate library, not a circuit** — do not parse as one |

Six circuits = **3 benchmarks x 2 variants** (`source` and `_transpiled`).
Both variants are real circuits and both get a row; table 4 has an explicit
`variant` column for exactly this reason.

---

## Table 4 — `silver/qasmbench/circuit.parquet`

**One row = one parsed circuit variant.** 6 rows.

| Column | Type | Source of the value |
| --- | --- | --- |
| `source_record_id` | string | the QASM archive member |
| `circuit_id` | string | stable circuit-variant id — **see open question 1** |
| `benchmark_name` | string | directory name, e.g. `qec_sm_n5` |
| `variant` | string | `source` or `transpiled` (from the filename suffix) |
| `register_declarations` | string | repeatable serialized `qreg`/`creg` description |
| `qubit_count` | int32 | sum of declared `qreg` sizes |
| `measurement_count` | int32 | **executed** measure operations (expanded) |
| `two_qubit_gate_count` | int32 | **executed** two-qubit operations (expanded) |

### Verified values

| circuit | registers | qubits | measurements | 2q gates |
| --- | --- | --- | --- | --- |
| `error_correctiond3_n5` source | `q[5]`, `c[5]` | 5 | 5 | 49 |
| `error_correctiond3_n5` transpiled | `q[5]`, `c[5]` | 5 | 5 | 49 |
| `qec_en_n5` source | `q[5]`, `c[5]` | 5 | 5 | 10 |
| `qec_en_n5` transpiled | `q[5]`, `c[5]` | 5 | 5 | 10 |
| `qec_sm_n5` source | `q[3]`, `a[2]`, `c[3]`, `syn[2]` | 5 | 5 | 4 |
| `qec_sm_n5` transpiled | `q[3]`, `a[2]`, `c[3]`, `syn[2]` | 5 | 5 | 4 |

**Free correctness check:** source and transpiled agree on every count for all
three benchmarks. Transpilation rewrites *how* gates are expressed, not how many
measurements or entangling operations the circuit performs. If a parser returns
different numbers for the two variants of one benchmark, the parser is wrong.

### Three counting traps

1. **A gate definition body is not executed code.**
   ```qasm
   gate syndrome d1,d2,d3,a1,a2 { cx d1,a1; cx d2,a1; cx d2,a2; cx d3,a2; }
   ```
   Those four `cx` lines are a *definition*, like a function body. The single
   call site `syndrome q[0],q[1],q[2],a[0],a[1];` is the execution — and it
   contributes **4** two-qubit operations, by expanding the body once.
   Only `qec_sm_n5.qasm` defines a custom gate.

2. **Register-addressed operations expand.** `measure a -> syn;` is **2**
   measurements (register `a` holds 2 qubits), not 1 statement.
   `measure q -> c;` is **3**. Total 5.
   The spec is explicit: *"count the expanded executed operations, not only the
   number of QASM statement lines."*

3. **`barrier` is not an operation.** It is a compiler fence. Do not count it.

---

## Table 6 — `silver/qasmbench/conditional_correction.parquet`

**One row = one recovery operation controlled by a measured syndrome.** 6 rows.

In plain English each row says: *"when the damage signature reads N, this
circuit repairs that qubit."*

| Column | Type | Source of the value |
| --- | --- | --- |
| `source_record_id` | string | the exact `if` statement in Bronze |
| `circuit_id` | string | FK into table 4 |
| `condition_register` | string | classical register the `if` reads |
| `condition_value` | int64 | integer that activates the operation |
| `gate` | string | correction gate name |
| `target_qubit` | string | qubit acted on |

### Where every row comes from

The entire input is these lines, and nothing else in the archive:

```qasm
if(syn==1) x q[0];
   |   |   |  +---- target_qubit       = "q[0]"
   |   |   +------- gate               = "x"
   |   +----------- condition_value    = 1
   +--------------- condition_register = "syn"
```

### Verified contents — all 6 rows

| circuit | register | value | gate | target |
| --- | --- | --- | --- | --- |
| `qec_sm_n5:source` | `syn` | 1 | `x` | `q[0]` |
| `qec_sm_n5:source` | `syn` | 2 | `x` | `q[2]` |
| `qec_sm_n5:source` | `syn` | 3 | `x` | `q[1]` |
| `qec_sm_n5:transpiled` | `syn` | 1 | `x` | `q[0]` |
| `qec_sm_n5:transpiled` | `syn` | 2 | `x` | `q[2]` |
| `qec_sm_n5:transpiled` | `syn` | 3 | `x` | `q[1]` |

**`error_correctiond3_n5` and `qec_en_n5` contribute zero rows.** A whole-archive
text scan finds `if(` on exactly 6 lines. That emptiness is a finding to record,
not a bug to chase.

### Why those three values, and why no `syn==0`

`qec_sm_n5` is a 3-qubit repetition code. Two parity checks:

```
a[0] checks q[0] vs q[1]  ->  syn[0]  (worth 1)
a[1] checks q[1] vs q[2]  ->  syn[1]  (worth 2)
```

| `syn` | which checks fired | so the broken qubit is | repair |
| --- | --- | --- | --- |
| 0 | neither | nothing is broken | **no line — correct** |
| 1 | check A only | `q[0]` (only in A) | `x q[0]` |
| 2 | check B only | `q[2]` (only in B) | `x q[2]` |
| 3 | both | `q[1]` (in both) | `x q[1]` |

Notebook section 9 derives this independently from the circuit's `cx`/`measure`
statements and confirms the file agrees on all three. So the rows are
**semantically** right, not merely parsed.

**The absence of `syn==0` is correct behaviour.** Do not record a missing row.

### `source_record_id`

```
qasmbench:<archive_member>#stmt=<statement_index>
```

e.g. `qasmbench:small/qec_sm_n5/qec_sm_n5.qasm#stmt=10`

A pure function of the Bronze bytes, so it is identical on every run.
It disambiguates two textually identical `if` lines, which a content hash
would not.

*Tradeoff, stated openly:* the index depends on this tokenizer's definition of a
statement (comments stripped, gate bodies excluded, split on `;`). Freeze that
definition with the code. `source_trace.parquet` also carries the **line
number**, a property of the file rather than the parser, as a human-checkable
cross-reference. Rejected alternative: keying on line number alone — two
statements can share a line (`cx d1,a1; cx d2,a1;`).

### Validation rules

Reject the row and write it to `results/part1/data_issues.parquet`:

| rule | check |
| --- | --- |
| `condition_register_declared` | register is a declared `creg` |
| `condition_value_in_range` | `0 <= value < 2**creg_width` (`syn[2]` -> 0..3) |
| `single_target` | contract stores exactly one `target_qubit` |
| `target_is_indexed_qubit` | target is `reg[i]`, not a whole register |
| `target_register_declared` | target register is a declared `qreg` |
| `target_index_in_range` | index is inside the declared size |

Accept, but record a warning:

| rule | check |
| --- | --- |
| `condition_register_measured` | register is written by some `measure` |
| `condition_before_measure` | the `if` comes after the measure that writes it |

**All rules pass on this release: 6 read, 6 accepted, 0 rejected, 0 issues.**
That is a result, not a reason to drop the rules — the rubric grades *tested*
rules. Unit-test each one against a tiny crafted bad input:

| rule | synthetic input that must fail it |
| --- | --- |
| `condition_value_in_range` | `creg syn[2];` + `if(syn==4) x q[0];` |
| `single_target` | `if(syn==1) cx q[0],q[1];` |
| `target_is_indexed_qubit` | `if(syn==1) x q;` |
| `target_register_declared` | `if(syn==1) x z[0];` |
| `target_index_in_range` | `qreg q[3];` + `if(syn==1) x q[7];` |
| `condition_register_declared` | `if(nope==1) x q[0];` |

### Contract checks on the finished table

- `source_record_id` unique
- no nulls in any of the six columns
- `circuit_id` is a subset of table 4's ids (cross-table FK)
- `(circuit_id, condition_register, condition_value)` unique

**Caveat on that last one:** it holds here but is **not** a law of OpenQASM. A
circuit could legally fire two corrections on one syndrome value
(`if(syn==1) x q[0]; if(syn==1) z q[0];`). It is a contract check on *this
release*, not a primary key. If it ever fires, record the issue — do not drop a
row, because the grain is "one recovery operation", so two operations are two
rows. The real key is `source_record_id`.

### Implementation notes

- **Do not hardcode `gate = "x"`.** Read whatever gate name appears. It happens
  to always be `x` here; the parser should not know that.
- **Keep the register name in `target_qubit`** — store `"q[0]"`, not `0`.
  `q[0]` and `a[0]` are different qubits (register-local indexing).
- **Do not deduplicate across variants.** Source and transpiled carry identical
  corrections at different statement positions. They are separate circuits.

---

## Open questions for the team

1. **`circuit_id` format — blocking, needs agreeing before either table is
   written.** Table 6's `circuit_id` is a foreign key into table 4's. Currently
   using `qasmbench:<benchmark>:<variant>`, e.g. `qasmbench:qec_sm_n5:source`.
   Whoever owns table 4 owns this string. If they prefer the archive member path,
   that is fine — but it must be decided, or the join silently breaks.

2. **Does table 5 (`stabilizer_check`) cover the `source` variant?** Its parity
   checks live inside the `gate syndrome {...}` body and need macro expansion to
   see. `data-sources.md` says "where explicit", which leaves room to populate
   table 5 from the transpiled variant only. Does not block table 6 — which
   covers both variants either way — but it makes the Gold join asymmetric and
   should be a deliberate decision.

3. **Heads-up: `measurement_count` means two different things in the schema.**
   In table 2 (`google_qec/experiment`) it is measurement *bits per shot* (209
   for d3). In table 4 it is executed *measure operations in the source code* (5).
   Same name, unrelated meanings. Easy footgun.

4. **For the Gold owner:** tables 4 + 5 + 6 together answer Part I analysis
   question 3 ("how does the repetition-code circuit map data qubits to ancillas,
   syndrome bits, and conditional corrections?") as a three-table join — which
   also satisfies the "at least one query joins three or more Gold tables"
   requirement.

---

## Proposed extra Silver column (not yet implemented)

`silver-tables.md` allows extra columns. Candidate for table 6:

**`condition_bits_set`** — `list<string>`, derived from `condition_value` plus
the declared register width:

| `condition_value` | `condition_bits_set` |
| --- | --- |
| 1 | `["syn[0]"]` |
| 2 | `["syn[1]"]` |
| 3 | `["syn[0]", "syn[1]"]` |

Table 5 has a `syndrome_bit` column holding exactly those strings, so this is a
**direct join key from a correction to the checks that triggered it** — which is
what makes analysis question 3 a clean SQL join instead of string arithmetic.

Legal in Silver: it is a faithful reinterpretation of `condition_value` using the
circuit's own `creg syn[2];` declaration. No cross-source join, no invented data.
