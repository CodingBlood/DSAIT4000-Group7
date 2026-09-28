# stabilizer_check

**Path:** silver/qasmbench/stabilizer_check.parquet
**Owner:** Sahil Jain
**Status:** Finished

## What one row represents
Each row is one stabilizer check found in a QASMBench circuit. That means one ancilla qubit that:

1) picks up parity from one or more data qubits through cx, and
2) gets measured into a bit that an if(...) later checks.

Both have to be true meaning an ancilla that's measured but never checked, or one that's never actually used, doesn't count.
## Where each column comes from
| Column             | Source                 | Notes |
|--------------------|------------------------|-------|
| `source_record_id` | Built from the file path, the line where the check was measured, and the ancilla name | Always the same for the same input, so reruns don't change it |
| `circuit_id`       | `qasmbench:<benchmark>:<variant>`| Same format tables 4 and 6 use, so we can join on it |
| `check_id`         | Just the ancilla qubit name (e.g. `a[0]`)| Already unique within its circuit |
| `ancilla_qubit`    | The qubit being measured/checked | Parsed straight from the QASM |
| `data_qubits`      | Every qubit that fed parity into that ancilla via `cx`| Sorted, no duplicates |
| `syndrome_bit`     | The classical bit the ancilla's result was written to | Only kept if something actually reads that bit later |
## Our understanding of this table

This table shows how each "ancilla" qubit looks for errors. An ancilla qubit takes parity information from neighboring data qubits (using `cx`) then is measured into a bit. Later in the circuit an `if` statement checks that bit and decides if something needs to be fixed (that fix is described in table 6).

So this table basically lists, in rows something that was hidden inside the QASM code: "this ancilla checks these data qubits and its result goes into this bit." That is helpful later because we can connect circuit → stabilizer_check → conditional_correction to answer one of the analysis questions (how data qubits are linked to checks and fixes).

QASMBench circuits are code, not real results. There is no labeled data to train a model on. So this table (and the other two QASMBench tables) is never used for the ML part
## Decisions made
1) We ignore `barrier` lines. Why: a barrier doesn't actually do anything to
  the qubits, it's just a note to the compiler. If we counted it as a real
  step, every line number after it would be off by one, and other tables
  would end up pointing at the wrong line.
2) We only count something as a real check if BOTH things are true: it
  collects data from other qubits, AND its result actually gets used by an
  `if` statement. Why: some circuits use similar-looking code (measuring
  things, moving data around) without actually doing error-correction. Only
  requiring one of the two conditions would wrongly count those as real
  checks; requiring both is what correctly separates real checks from
  look-alike code.
3) We keep the "source" and "transpiled" versions of each circuit separate
  instead of merging them. Why: they're technically different files with
  different line numbers and structure, even though they represent the same
  logical circuit — merging them would lose the ability to trace a row back
  to the exact file it came from, and tables 4 and 6 already keep them
  separate too, so merging ours would break the matching between tables. 

## Open questions
-

## Validation checks implemented
- [ ]
