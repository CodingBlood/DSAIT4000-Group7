# Decision Log

One entry per decision. Add newest at the top.

## [YYYY-MM-DD] <short title>
**Table/area:**
**Decision:**
**Reason:**
**Alternatives considered:**

## [2026-02-26] <short Golden Relation QEC - shot connection>
**Table/area: shot table**
**Decision: It was decided to connect table 2 and 3 based on shot index.**
**Reason: This way, for each experiment, the information for each shot would be available**
**Alternatives considered:**

## [2026-02-26] <short Golden Relation QEC - decoder connection>
**Table/area: shot_prediction table**
**Decision:  for each decoder-type it was chosen to connect it with its corresponding shot via shot_id in a separate table**
**Reason: This way, for each shot, it becomes really easy to derive the boolean value for each decorder it gave**
**Alternatives considered:**
## [2026-10-06] QASM record locators are line based, not statement index
**Table/area: circuit (4), stabilizer_check (5), conditional_correction (6)**
**Decision: source_record_id uses `#L<line>.<ordinal>`, and all three QASMBench tables draw statements from one shared tokenizer.**
**Reason: tables 5 and 6 had separate tokenizers that counted statements differently (one skipped `barrier` and counted the `gate` header, the other did the reverse). On qec_sm_n5.qasm the two differences cancelled, so it looked fine; on the transpiled variant the same `if(syn==1)` line was statement 13 to one table and 14 to the other. A line number belongs to the file rather than to a parser, so it cannot drift, and a reader can verify it by counting lines. Fixed while no Silver output existed, which made it free.**
**Alternatives considered: keeping the statement index and freezing the tokenizer definition (rejected, too easy to break silently); keying on line number alone (rejected, OpenQASM allows several statements per line, hence the ordinal suffix).**

## [2026-10-06] Two-qubit gate count derived from operand count
**Table/area: circuit (4)**
**Decision: an operation with exactly two qubit operands is a two-qubit operation. No hardcoded gate-name list.**
**Reason: handles cx, cz, swap and anything else without us maintaining a list. Cross-checked against an independent implementation that did use a hardcoded list; both give 49 / 10 / 4.**
**Alternatives considered: a hardcoded set of two-qubit gate names (works, but needs updating for any new gate).**

## [2026-10-06] Gate names validated against the archive's own qelib1.inc
**Table/area: circuit (4)**
**Decision: read the legal gate names from qelib1.inc in the Bronze archive, plus the circuit's own definitions. Unknown names warn rather than reject.**
**Reason: the archive documents its own gate set, so we do not invent a list. A name we do not recognise is more likely our gap than a broken circuit, so the row is kept and flagged.**
**Alternatives considered: rejecting unknown gates (too aggressive); not checking at all (loses a cheap signal).**

## [2026-10-06] Source and transpiled circuits are kept separate
**Table/area: circuit (4), conditional_correction (6)**
**Decision: both variants get rows. No deduplication, even though their corrections are identical.**
**Reason: transpilation produces a genuinely different program, and silver-tables.md gives table 4 a `variant` column for this. Deduplicating would also break the trace back to a specific Bronze member.**
**Alternatives considered: keeping only the transpiled variant (loses traceability to the source file).**

## [2026-10-06] Silver table 6 keeps exactly the six contract columns
**Table/area: conditional_correction (6)**
**Decision: no extra derived columns in Silver. The mapping from condition_value to the syndrome bits it requires is done in SQL at the Gold layer.**
**Reason: Gold stores condition_value directly, so `condition_value >> syndrome_index & 1` answers it in the analysis query. A Python helper existed briefly and was removed as dead code. These six tables are also the stable format handed to the visualization assignment, so staying minimal is safer.**
**Alternatives considered: a `condition_bits_set` list column in Silver (allowed by the spec, but unnecessary once the Gold schema split syndrome_bit into register and index).**

## [2026-10-06] Documented-header mismatch is a warning, not a rejection
**Table/area: syndrome_observation (1)**
**Decision: README.txt documents the first column as `label` while every CSV header says `labels`. Recorded in data_issues as severity=warning, action=accepted, once per archive.**
**Reason: brief.md lists this as a minimum check. The header is authoritative and the data is usable, so no rows are rejected, but the mismatch stays visible. Recorded once rather than seven times because it is a property of the release, not of any one CSV.**
**Alternatives considered: raising and stopping the run (too aggressive for a documentation defect); ignoring it (loses a required check).**

## [2026-10-06] experiment_id spells the fault rate as the filename does
**Table/area: syndrome_observation (1)**
**Decision: build experiment_id from the raw filename text, giving `syndrome_d3_pfr0.000010`.**
**Reason: formatting the parsed float produced `syndrome_d3_pfr1e-05` for the smallest rates. Deterministic either way, but scientific notation inside an identifier is a trap for anyone joining on it. physical_fault_rate stays a float.**
**Alternatives considered: formatting the float to fixed decimals (works, but the filename text is faithful to the source and needs no format choice).**

## [2026-10-06] QASMBench circuits have no foreign key to any experiment
**Table/area: Gold, rejected relationship**
**Decision: investigated and rejected a join between QASMBench circuits and the Google or simulated experiments.**
**Reason: no shared identifier in either direction; a distance-three Google experiment uses 17 qubits while error_correctiond3_n5 declares 5; and QASMBench has no shots or labels, so there is nothing to join to. Shared vocabulary ("distance 3") is not a shared identifier.**
**Alternatives considered: matching on code distance (rejected, the evidence above); matching on qubit count (rejected, they do not agree).**
