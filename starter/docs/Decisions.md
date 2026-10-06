# Decision Log

One entry per decision. Add newest at the top.

## [YYYY-MM-DD] <short title>
**Table/area:**
**Decision:**
**Reason:**
**Alternatives considered:**

## [2026-10-06] Rejected join: QASMBench circuits to Google or syndrome experiments
**Table/area:** circuit, experiment, syndrome_experiment
**Decision:** QASMBench circuits are stored as their own entities with no foreign key to any experiment.
**Reason:** No shared identifier exists. results/part1/analysis/rejected_join_qasmbench.csv shows 0 matches on circuit_id, qubit count, measurement count and number of checks. The only join that returns rows is matching "d3" in the benchmark name to code distance, and that gives 400000 Google shot rows and 151196 syndrome rows from 2 circuits, a many-to-many result with no row-level meaning.
**Alternatives considered:** joining on code distance taken from the benchmark name, and joining on qubit or measurement counts.

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