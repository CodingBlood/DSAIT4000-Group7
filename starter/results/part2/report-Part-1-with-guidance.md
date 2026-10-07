# Group – 7 – DME PROJECT – PART I

# Overview

For our group assignment, we were given three heterogeneous data sources from quantum error correction (QEC). They had different formats, different levels of detail and no shared identifiers. Our job was to understand each source, transform it into clean and readable tables, and organise those tables so they can be analysed further, both with SQL and with machine learning in Part II.

The three sources are:

1.  **Google surface-code data.** Real experimental shots with detector events and the predictions of four decoders.

2.  **Syndrome data.** Simulated syndrome patterns for a distance-3 code at seven physical fault rates.

3.  **QASMBench circuits.** Small quantum circuits written as OpenQASM text.

They use different formats, describe different things and share no identifiers. To turn them into one usable dataset we carried out the following pre-specified tasks:

1.  **Store the raw files safely (Bronze).** We kept the three original zip files unchanged and verified each one by size and SHA-256 hash.

2.  **Understand and clean each source (Silver).** We worked out what a row means in each source, then produced six validated tables in which every row can be traced back to its exact source file and row (or line).

3.  **Design a relational model (Gold).** We decided how the sources relate (and where they do not) and loaded 13 tables into PostgreSQL in a single transaction.

4.  **Analyse the data with SQL**. We answered the three required questions, including one that joins three or more tables.

5.  **Prepare the machine-learning inputs.** We built the two ML tables for Part II from Gold views only.

Altogether we processed \_\_\_\_ rows, and all of them were accepted. The full run from raw files to ML tables and analyses takes about \_\_seconds.

**Work distribution.** The work was split by data source. Karthik and Alex handled the Google and syndrome sources from Bronze through to Gold. Divit, Siddhart and Sahil handled the QASMBench source. For Part II, the split followed the same lines: Divit, Siddhart and Sahil built the models for the syndrome data, and Karthik and Alex built the models for the Google data. Smaller tasks, such as documentation, testing and reviews, were shared out according to each person's workload at the time.

# Sources and what makes each hard

## Data Overview


| **Source**          | **Bronze file**                 | **Size**   | **Content**                                      |
|---------------------|---------------------------------|------------|--------------------------------------------------|
| Google surface code | google-surface-code-curated.zip | 14,638,673 | Experiment detector data and decoder predictions |
| QEC syndromes       | syndromes_dataset.zip           | 358,017    | Syndrome histograms at 7 fault rates             |
| QASMBench           | qasmbench-qec.zip               | 144,172    | 6 circuit files plus a gate library              |

## **Google surface-code source**

- The Google data contains 5 directories, one for each experiment (4 with code distance 3 and 1 with distance 5). Each directory has a `properties.yml` file with the experiment's metadata, which becomes the `experiment` table in the Silver stage.
- Each experiment row is identified by `experiment_id` and a `source_record_id` that links back to its `properties.yml` file.
- One experiment row describes:
  - the chip region where the experiment was run (`center_row`, `center_col`),
  - the measurement basis (`basis`) and the error-protection level (code distance),
  - the number of runs (`shots`) and the number of times the cycle was repeated within each shot (`rounds`),
  - the number of raw readout bits per shot (`measurement_count`) and the number of detector-event bits per shot (`detector_count`).
- Each experiment has many shots, which gives the one-to-many relationship used in the Gold schema.
- **Problem we found :** the detector events are stored as packed bits (the Stim `b8` format), with 8 bits per byte, the lowest bit first, and zero padding in the last byte. Unpacking them in the wrong order gives wrong values without any error message. Detector events, raw measurements and decoder predictions are also three different things and must not be mixed up. 

## **Syndrome source**

- The syndrome data comes as one zip file with 7 CSV files, one for each physical fault rate (1e-5, 5e-5, 1e-4, 5e-4, 1e-3, 5e-3 and 1e-2). All of them are for code distance 3.
- The file name encodes the experiment, for example `d-3_pfr-0.005000_nb-10M.csv`: code distance 3, physical fault rate 0.005 and 10 million simulated shots (`nb-10M`).
- Each file has three columns: `labels`, `syndromes` and `quantity`.
  - `labels` says whether a logical error occurred.
  - `syndromes` is a text string holding a nested tuple of 4 rounds with 4 binary check values each, which we turn into 16 bits.
  - `quantity` is the number of simulated shots that one row stands for.
- One row is an aggregate: a distinct syndrome pattern with its label and a count. In total the 7 files stand for 70 million shots but contain only 75,598 rows.
- **Problem we found :** the rows are weighted, so the number of rows is not the number of observations. Any rate or average that ignores `quantity` is wrong. We kept `quantity` as a weight (and later as `sample_weight` for the ML tables), and we checked that every file adds up to exactly 10,000,000 observations. We also did not expand the data to 70 million individual rows.

## **QASMBench source**

- There are 3 benchmarks, each in two variants (source and transpiled), giving 6 circuit files. There is also qelib1.inc, which is the standard gate library and not a circuit.
- The benchmarks are error_correctiond3_n5, qec_en_n5 and qec_sm_n5. All have 5 qubits.
- Only qec_sm_n5 contains classical feedback. It has 3 data qubits, 2 ancilla qubits, a 2-bit syndrome register and 3 conditional X corrections.
- **Problem we found :** the text contains gate definitions (gate syndrome {...}). A definition is not an operation, so counting it would double-count the gates.

# Architecture

*Guidance: show the main stages, how data flows between them, and which code does each step.*

## Pipeline and data flow

Our architecture follows the stages of the `quantum_lake_student` package. Everything starts in `cli.py`, where `run_pipeline` calls each stage in order, and `make run` triggers it.

1. **Register (`register_sources.py`).** The three course data sources are checked against the course `bundle-manifest.json` (file size and SHA-256). Unsafe zip members are rejected. Verified files are stored unchanged in the Bronze layer in MinIO.
2. **Prepare (`prepare_data.py`).** Each source is read from Bronze and cleaned, validated and converted into Silver Parquet tables: six tables, plus a shared `data_issues` table and a `source_trace` table. Every Silver row gets a `source_record_id` so it can be traced back to its original file.
3. **Load (`load_postgres.py`).** Once all Silver tables exist, the Gold schema in PostgreSQL is created and filled from Silver. This uses bulk `COPY` inside one transaction, and tables are emptied and reloaded on each run. Gold has 13 tables.
4. **Build ML tables (`build_ml_tables.py`).** SQL views over Gold only are exported as Parquet files in `ml/`. Contract checks run on them before they are written.
5. **Analyse (`analysis.py`).** Every SQL file in `sql/analysis/` is run on Gold and its result is saved as a CSV in `results/part1/analysis/`.

The run ends by writing `run.json` with the row counts, step timings and the git revision. The ML tables are the only input to Part II (`train.py`). A full run takes about 34 seconds.

```mermaid
flowchart LR
    A[Course data<br/>Google, Syndromes, QASMBench] -->|size + SHA-256 check<br/>safe zip members| B[(Bronze<br/>raw zips in MinIO)]
    B -->|parse, validate,<br/>add source_record_id| C[(Silver<br/>Parquet tables<br/>+ data_issues + source_trace)]
    C -->|bulk COPY, one transaction<br/>TRUNCATE and reload| D[(Gold<br/>PostgreSQL, 13 tables)]
    D -->|SQL analysis files| E[Analysis CSVs<br/>results/part1/analysis]
    D -->|SQL views + contract checks| F[ML Parquet tables<br/>ml/]
    F --> G[Part II<br/>train.py]
```

## Stages, key functions and techniques

| Stage | File | Key functions | What they do / technique used |
|---|---|---|---|
| Orchestrate | `cli.py` | `run_pipeline`, `command_run`, `git_revision` | Runs all stages in order, times each one and records the code revision in `run.json`. |
| Register → Bronze | `register_sources.py` | `load_expected_manifest`, `is_safe_member`, `run` | Compares size and SHA-256 with the course manifest and rejects unsafe zip member paths before storing the raw files. |
| Prepare: Google | `prepare_data.py` | `read_b8_rows`, `padding_is_zero`, `count_set_bits_in_bytes`, `build_experiment_rows`, `build_shot_rows` | Unpacks Stim `b8` bit-packed shots (little-endian), checks padding bits and counts detector bits to produce experiment and shot tables. |
| Prepare: Syndromes | `prepare_data.py` | `parse_filename`, `parse_syndrome_bits`, `build_rows` | Reads metadata from CSV file names, turns the 16-bit syndromes into bit columns and keeps weighted counts instead of expanding to 70M rows. |
| Prepare: QASMBench | `prepare_data.py` | `qasm_statements`, `qasm_registers`, `qasm_gate_definitions`, `qasm_expanded_operations`, `find_stabilizer_checks`, `validate_check`, `build_conditional_correction_rows` | Parses the OpenQASM text, expands gate definitions from `qelib1.inc`, finds stabilizer checks and conditional corrections, and builds circuit tables. |
| Prepare: tracing | `prepare_data.py` | `shot_issue`, `source_trace` rows | Logs problems into `data_issues` and keeps `source_record_id` so every row can be traced back to its source. |
| Load → Gold | `load_postgres.py` | `copy_rows`, `load_experiment`, `load_decoders`, `load_shots`, `load_syndromes`, `load_qasm` | Bulk `COPY FROM STDIN` into 13 Gold tables in one transaction, with `TRUNCATE ... RESTART IDENTITY CASCADE` so reruns give the same result. |
| Build ML tables | `build_ml_tables.py` | `get_google_MlTables`, `get_syndrome_MlTable`, `validate_google_ml_table`, `validate_syndrome_ml_table` | Uses SQL views over Gold only, validates the table contract and writes Parquet to `ml/`. |
| Analyse | `analysis.py` | `run` | Executes every `.sql` file in `sql/analysis/` and writes the results as CSV. |
| Part II helpers | `ml.py`, `train.py` | `unpack_little_endian_bits`, `google_data_split`, `syndrome_data_split`, `weighted_logical_error_rate` | Shared splits and metrics used by the models; these are outside the Part I pipeline. |
# Silver: cleaning, validation, traceability

*What goes here: Show that bad data is handled by clear rules and every row can be traced to its source.*

## Silver tables

*What goes here: The six tables, their source, what one row means and the row counts.*

## Cleaning and validation rules

*What goes here: Reject (error) versus warn (warning), with a few example rules per source. Full rule lists can go in an appendix.*

## Traceability

*What goes here: The source_record_id formats, source_trace.parquet, data_issues.parquet and how a row is followed back to its Bronze file.*

# Gold schema and key decisions

## Gold design by source

### Google

Since each experiment has a shot related to it, it was decided to connect each individual experiment (google experiment) with its shot (Google shots). The google experiment table is left untouched and its experiment_id is used as FK in the shot table.

For each shot it is also known what the four decoders have predicted in that particular shot. To prevent redundancy and stimulate clarity, it was decided to give the prediction its own table. This table contains the four decoders. The table that connect decoder table with shot table is the shot_prediction table. It uses the shot_id from the shot table and decoder_id from the decoder table to access each individual prediction for that particular combination of the shot_id and decoder. This design choice implies a trade-off of an additional join across 2 extra tables instead of writing one row. However, this makes a cross-comparison for decoders far simpler, and in case and additional decoder needs to be added, all the tables (except decoder) remain unchanged.

### Syndrome

The syndrome data is stored in three tables, one for the experiments, one for the distinct syndrome patterns and one for the observations that connect them.

- **syndrome_experiment** has one row per experiment, which means one per CSV file (7 rows). It stores the code distance and the physical fault rate, which are read from the file name. The distance is stored here once, so it does not repeat on every observation.
- **syndrome_pattern** has one row per distinct 16-bit syndrome. The bits are stored as 16 bytes of 0 and 1 and must be unique, and the round count and check count are both checked to be 4. The `pattern_id` is a hash of the bits (`pat_` followed by 12 hexadecimal characters), so the same syndrome always gets the same id.
- **syndrome_observation** has one row per row of the source CSV (75,598 rows). It points to its experiment and its pattern, and stores the logical-error label and the `quantity`, which is the number of simulated shots the row stands for. Its key is the `source_record_id`, so every observation traces back to a file and row.

We store the pattern once and keep the label and the count on the observation, for three reasons:

- The same syndrome occurs many times. In the analysis query Q1 there are 75,598 rows but only 50,439 distinct (experiment, pattern) combinations, so some syndromes occur in more than one row of the same experiment, with different labels. The label therefore belongs to the observation, not to the pattern.
- The `quantity` has to stay on the observation, because the same pattern has a different count for each experiment and label. Keeping it there makes weighted sums a simple `SUM(quantity)`.
- Storing the bits once saves space and lets analyses group by pattern directly.

The price is one extra table and two joins whenever we need the bits together with the label. In return, the database prevents impossible values: a quantity must be positive, a pattern must have exactly 4 rounds and 4 checks, and an observation cannot point to an experiment or a pattern that does not exist.

### QASMBench

The QASMBench tables form their own group in Gold. They are linked to each other by `circuit_id`, which has the form `qasmbench:<benchmark>:<variant>`, and have no foreign key to the Google or syndrome tables (see the rejected relationship section).

The central table is **circuit**. It holds one row per circuit file (6 rows: 3 benchmarks, each in a `source` and a `transpiled` variant), with the qubit, measurement and two-qubit gate counts. A check constraint only allows the variants `source` and `transpiled`, and the pair (`benchmark_name`, `variant`) must be unique.

The Silver `circuit` table stores the registers as text (for example `qreg q[3]; qreg a[2]`). In Gold this text is split into two tables:

- **circuit_register** has one row per declared register (`q`, `a`, `c`, `syn`) with its type (`qreg` or `creg`), size and declaration order.
- **circuit_qubit** has one row per individual qubit (`q[0]`, `q[1]`, `a[0]`, ...). Only quantum registers create qubit rows.

These two tables let the database check the other tables. An ancilla, a data qubit or a correction target cannot be stored unless that qubit exists in the same circuit, and a syndrome or condition register cannot be named unless it is declared.

The **stabilizer_check** table has one row per parity check. It stores the ancilla qubit that does the check, plus the classical register and index where its result is written. Silver stores this as one text value (`syn[0]`). Gold splits it into `syndrome_register` and `syndrome_index`, because the analysis query needs the index as a number.

A check reads from one or more data qubits. A list in one column could not be joined or constrained, so they go in their own junction table, **stabilizer_check_data_qubit**, with one row per (check, data qubit).

The **conditional_correction** table has one row per `if(register == value) gate target` statement. Its condition register and its target qubit are foreign keys into `circuit_register` and `circuit_qubit`, so a correction can only point at things that exist in its circuit.

Design choices:

- This adds extra tables and joins compared with one wide circuit table. In return, the database enforces consistency, and the query "which data qubits feed which check, and which correction repairs them" is a plain join.
- Source and transpiled circuits are both kept, because they are separate files with different statements. Merging them would break the link back to the exact file.
- The value `syn == 0` has no correction row. No check fired, so there is nothing to repair.

## ER diagram of the Gold tables

Gold Design

```mermaid
erDiagram
  EXPERIMENT ||--o{ SHOT : "has many"
  SHOT ||--o{ SHOT_PREDICTION : "has many"
  DECODER ||--o{ SHOT_PREDICTION : "makes"

  EXPERIMENT {
    string experiment_id PK
    string source_record_id
    string basis
    int distance
    int rounds
    bigint shots
    int center_row
    int center_col
    int measurement_count
    int detector_count
  }
  SHOT {
    string shot_id PK
    string source_record_id
    string experiment_id FK
    bigint shot_index
    bytea measurement_bits
    bytea sweep_bits
    bytea detector_bits
    int detector_event_count
    bool actual_observable_flip
  }
  DECODER {
    string decoder_id PK
    string decoder_name
  }
  SHOT_PREDICTION {
    string shot_id FK
    string decoder_id FK
    bool predicted_flip
  }
```

Syndrome

```mermaid
erDiagram
  SYNDROME_EXPERIMENT ||--o{ SYNDROME_OBSERVATION : "has many"
  SYNDROME_PATTERN ||--o{ SYNDROME_OBSERVATION : "appears in"

  SYNDROME_EXPERIMENT {
    string experiment_id PK
    int distance
    float physical_fault_rate
  }
  SYNDROME_PATTERN {
    string pattern_id PK
    bytea syndrome_bits
    int round_count
    int check_count
  }
  SYNDROME_OBSERVATION {
    string source_record_id PK
    string experiment_id FK
    string pattern_id FK
    bool logical_error_label
    bigint quantity
  }
```

QASMBench 

```mermaid
erDiagram
  CIRCUIT ||--o{ CIRCUIT_REGISTER : "declares"
  CIRCUIT ||--o{ CIRCUIT_QUBIT : "contains"
  CIRCUIT_REGISTER ||--o{ CIRCUIT_QUBIT : "holds"
  CIRCUIT ||--o{ STABILIZER_CHECK : "has"
  CIRCUIT ||--o{ CONDITIONAL_CORRECTION : "has"
  STABILIZER_CHECK ||--o{ STABILIZER_CHECK_DATA_QUBIT : "reads"
  CIRCUIT_QUBIT ||--o{ STABILIZER_CHECK_DATA_QUBIT : "is data qubit of"
  CIRCUIT_QUBIT ||--o{ STABILIZER_CHECK : "is ancilla of"
  CIRCUIT_REGISTER ||--o{ STABILIZER_CHECK : "stores result in"
  CIRCUIT_REGISTER ||--o{ CONDITIONAL_CORRECTION : "is tested by"
  CIRCUIT_QUBIT ||--o{ CONDITIONAL_CORRECTION : "is target of"

  CIRCUIT {
    string circuit_id PK
    string source_record_id
    string benchmark_name
    string variant
    int qubit_count
    int measurement_count
    int two_qubit_gate_count
  }
  CIRCUIT_REGISTER {
    string circuit_id PK, FK
    int register_order PK
    string register_name
    string register_type
    int register_size
  }
  CIRCUIT_QUBIT {
    string circuit_id PK, FK
    string qubit_name PK
    string register_name FK
    int qubit_index
  }
  STABILIZER_CHECK {
    string circuit_id PK, FK
    string check_id PK
    string source_record_id
    string ancilla_qubit FK
    string syndrome_register FK
    int syndrome_index
  }
  STABILIZER_CHECK_DATA_QUBIT {
    string circuit_id PK, FK
    string check_id PK, FK
    string data_qubit PK, FK
  }
  CONDITIONAL_CORRECTION {
    string source_record_id PK
    string circuit_id FK
    string condition_register FK
    bigint condition_value
    string gate
    string target_qubit FK
  }
```

## Key decisions

*What goes here: A table: decision, reason, alternative not chosen. Include the transaction load, the deterministic ids and the ML views.*

# The rejected relationship

QASMBench is different in kind from the other two sources. A QASMBench file is a **circuit**: a description of the operations a quantum computer should run. The Google and syndrome files are **outputs**: measurements and results from running experiments. A circuit has no shots, no syndromes and no error labels, so drawing a relationship between the two kinds of data has no real meaning.

We still tested it rather than assuming. The query `starter/sql/analysis/rejected_join_qasmbench.sql` tries several ways of joining the circuits to the experiments, and its output is saved in `starter/results/part1/analysis/rejected_join_qasmbench.csv`:

| Candidate join | Matching rows |
|---|---|
| Circuit id = Google experiment id | 0 |
| Circuit id = syndrome experiment id | 0 |
| Measurement count, qubit count or checks per circuit = the Google values | 0 |
| Benchmark name contains `d3`, joined to syndrome rows | 151,196 |
| Benchmark name contains `d3`, joined to Google shots | 400,000 |

Every join based on a real identifier or property finds nothing. The only one that returns rows matches on the text "d3" in a benchmark name. Here "d3" is read as code distance 3, and only one benchmark (`error_correctiond3_n5`, in its source and transpiled variants) has it. That one match pairs the same two circuit files with every distance-3 row, so the numbers are large but meaningless, and any totals computed over them would be wrong.

**Decision.** QASMBench stays in its own tables, linked to each other by `circuit_id`, with no foreign key to either experiment source. The decision is also recorded in `docs/Decisions.md`.

# Data-quality findings

## Overall

All three sources passed validation without a single rejected row. In total 325,619 rows were read and 325,619 were accepted.

| Table | Source | Read | Accepted | Rejected |
|---|---|---|---|---|
| `experiment` | Google | 5 | 5 | 0 |
| `shot` | Google | 250,000 | 250,000 | 0 |
| `syndrome_observation` | Syndromes | 75,598 | 75,598 | 0 |
| `circuit` | QASMBench | 6 | 6 | 0 |
| `conditional_correction` | QASMBench | 6 | 6 | 0 |
| `stabilizer_check` | QASMBench | 4 | 4 | 0 |
| **Total** | | **325,619** | **325,619** | **0** |

The counts come from `results/part1/row_counts.json`. The `data_issues` table holds 80 warnings, and all of them come from QASMBench (see below). The rest of this section describes the findings for each source.

## QASMBench

### Row counts

All QASMBench rows passed validation: nothing was rejected.

| Table | Read | Accepted | Rejected |
|---|---|---|---|
| `circuit` | 6 | 6 | 0 |
| `conditional_correction` | 6 | 6 | 0 |
| `stabilizer_check` | 4 | 4 | 0 |

Only two of the six circuit files produce rows in `conditional_correction` and `stabilizer_check`: the source and transpiled versions of `qec_sm_n5`. This is the only benchmark with classical feedback. The other four files (`error_correctiond3_n5` and `qec_en_n5`, each in two variants) contain no `if` statements and no parity checks, so they correctly produce a `circuit` row and nothing else. These empty results are not data loss.

| File | `circuit` | `conditional_correction` | `stabilizer_check` |
|---|---|---|---|
| `error_correctiond3_n5` (source, transpiled) | 1 each | 0 | 0 |
| `qec_en_n5` (source, transpiled) | 1 each | 0 | 0 |
| `qec_sm_n5` (source, transpiled) | 1 each | 3 each | 2 each |

We checked the counts against the raw files. A text scan of the whole archive finds `if(` on exactly 6 lines, which matches the 6 rows in `conditional_correction`. The 4 stabilizer checks are the 2 parity checks of `qec_sm_n5`, counted once per variant. Source and transpiled files also give the same qubit, measurement and two-qubit gate counts for every benchmark, which is a useful consistency check because transpiling changes how gates are written, not how many there are.

### Warnings and rejected rows

No QASMBench row was rejected, but `data_issues` contains 80 warnings. All of them come from one rule, `qasm_circuit_known_gate`, which warns when a gate name is not defined in the circuit or in the gate library `qelib1.inc`.

| File | Warnings | Gate name flagged | Meaning |
|---|---|---|---|
| `error_correctiond3_n5_transpiled` | 62 | `sx` | Real finding |
| `qec_en_n5_transpiled` | 12 | `sx` | Real finding |
| `qec_sm_n5` and `qec_sm_n5_transpiled` | 3 each (6 in total) | `if` | Probably a false positive |

- **`sx` (74 warnings):** this gate appears only in the transpiled files. The transpiler introduced it, and `qelib1.inc` does not declare it. We accepted these rows because an unknown gate name is more likely a gap in our gate list than a broken circuit. This is why the rule is a warning and not an error.
- **`if` (6 warnings):** `if` is the keyword for a conditional statement, not a gate. The count is 3 per file, which equals the number of conditional statements in each file, so the rule is most likely treating each `if` statement as a gate. These rows are correct and also appear in `conditional_correction`.

Each validation rule is also tested with a crafted bad input in `tests/test_qasmbench.py`. This shows that the rules work even though none of them rejected a real row.

### Known gaps

- The rule `qasm_circuit_known_gate` does not recognise the `if` statement, so it produces 6 warnings that are not real problems.
- `run.json` reports `issue_count: 0` for every stage, while `data_issues.parquet` holds 80 warnings. The counter only counts rejections, not warnings.
- Only one of the three benchmarks contains classical feedback, so the correction and check logic has been checked on a single circuit family.

# Worked traces

## Google shot trace

A trace proves reproducibility: it shows that any ML example can be followed back to its original record. An example of the Google trace can be found in `results/part1/trace_examples.json`. It is generated by `build_trace_examples.py` and builds the full chain ML, Gold, Silver and Bronze for one shot (experiment `google_surface_code_bX_d3_r25_center_3_5`, shot index 1).

- **ML:** a row is selected from the ML view by `experiment_id` and `shot_index`. This gives the `example_id` (`ml_f1d5fd50f9b1`) and the `shot_id` (`google_shot_4959c10c62f4`).
- **Gold:** the `shot` table is read by `shot_id`. It contains a `source_record_id` that links back to Silver and Bronze. The `experiment` row is read using the shot's `experiment_id` and has its own `source_record_id` (`exp_dc1c8c81dc52`). The four decoder predictions are read from `shot_prediction` by `shot_id`.
- **Silver:** the `shot` and `experiment` Parquet tables are filtered by the `source_record_id` values taken from the Gold shot and experiment rows.
- **Bronze:** the same `source_record_id` values are used to find the rows in the shared source trace table. One shot maps to several files in the original archive, each with the record locator `shot_index=1`: the measurements, the detection events, the actual observable flips and the predictions of each decoder.

| Step | Table or file | Key used |
|---|---|---|
| ML | `ml/ml_google_decoder_example.parquet` | `example_id` = `ml_f1d5fd50f9b1` |
| Gold | `shot`, `experiment`, `shot_prediction` | `shot_id` = `google_shot_4959c10c62f4`, `experiment_id` |
| Silver | `silver/google_qec/shot.parquet` and `experiment.parquet` | `source_record_id` |
| Bronze | `source_trace.parquet` | `source_record_id`, archive member and `shot_index=1` |

## Syndrome trace

An example for the syndrome trace can be found in the same file.

- **ML:** an example row is selected from the ML view using the `physical_fault_rate` (0.005). This gives the `example_id` (`0001d337430c71924ebbea40f16c760d`) and the `source_record_id` (`syn_e0e8e6490262`).
- **Gold:** for this `source_record_id`, the `syndrome_observation` row is read. It contains the `pattern_id` (`pat_8a401b2b5886`), which is used to read the `syndrome_pattern` row. The `experiment_id` (`syndrome_d3_pfr0.005`) is used to read the `syndrome_experiment` row.
- **Silver and Bronze:** the Silver table `syndrome_observation` and the source trace table are filtered on the same `source_record_id`. The Bronze row points to the archive member `d-3_pfr-0.005000_nb-10M.csv`, row 18075.

This shows the trace by explicitly extracting the keys and foreign keys at every step.

# SQL analyses

The assignment asks three questions, which we answer with reproducible SQL against the Gold tables. The queries are in `starter/sql/analysis/`, and the analysis stage runs them and saves each result as a CSV file in `starter/results/part1/analysis/`. We describe associations in the data and do not claim that one variable causes another.

## Q1: How do weighted syndrome frequency and logical-error labels change with physical fault rate?

**Query:** `q1_syndrome_by_fault_rate.sql`. It joins `syndrome_observation`, `syndrome_experiment` and `syndrome_pattern` (three Gold tables), groups by experiment, and weights every row by its `quantity` so that each row counts as many times as it was observed.

| Fault rate | CSV rows | Distinct syndromes | Weighted observations | Logical-error rate | Share of all-zero syndrome | Logical errors with all-zero syndrome |
|---|---|---|---|---|---|---|
| 1e-5 | 68 | 68 | 10,000,000 | 0.000234 | 0.99873 | 0 |
| 5e-5 | 215 | 202 | 10,000,000 | 0.001154 | 0.99364 | 0 |
| 1e-4 | 491 | 445 | 10,000,000 | 0.002291 | 0.98741 | 0 |
| 5e-4 | 1,407 | 1,228 | 10,000,000 | 0.011440 | 0.93841 | 2 |
| 1e-3 | 2,854 | 2,387 | 10,000,000 | 0.022702 | 0.88061 | 2 |
| 5e-3 | 20,887 | 14,643 | 10,000,000 | 0.103950 | 0.53097 | 210 |
| 1e-2 | 49,676 | 31,466 | 10,000,000 | 0.186527 | 0.28396 | 1,010 |

**What it shows:**

- The weighted logical-error rate rises steadily with the physical fault rate, from 0.0234% at 1e-5 to 18.7% at 1e-2. At low fault rates it is about 23 times the fault rate (0.000234 / 0.00001 = 23.4; 0.0227 / 0.001 = 22.7). At 1e-2 the ratio drops to about 18.7, so the rise slows at higher fault rates.
- The share of observations with the all-zero syndrome falls from 99.9% to 28%, so a rising fault rate goes together with more non-trivial syndromes.
- The number of distinct syndromes grows from 68 to 31,466. This is why the data are stored as weighted rows and why the Gold design keeps each pattern once.
- A few logical errors occur with an all-zero syndrome (2 at 5e-4, up to 1,010 rows at 1e-2). The 16 syndrome bits cannot reveal these errors.
- Each file adds up to exactly 10,000,000 weighted observations, which confirms the weights are handled correctly.
- Only distance 3 is available for the syndrome data, so this comparison says nothing about different code distances.

## Q2: How do the supplied decoder logical-error rates compare by code distance and distance-three processor location?

**Query:** `q2_decoder_error_rates.sql`. It joins `shot`, `experiment` and `shot_prediction` (three Gold tables) and groups by distance, patch centre and decoder. A decoder is counted as wrong on a shot when its predicted flip differs from the actual observable flip. The error rate is the share of shots where it is wrong.

| Distance | Patch centre | Belief matching | Correlated matching | PyMatching | Tensor-network contraction |
|---|---|---|---|---|---|
| 3 | (3, 5) | 0.4025 | 0.4194 | 0.4342 | 0.4023 |
| 3 | (5, 3) | 0.4159 | 0.4252 | 0.4432 | 0.4130 |
| 3 | (5, 7) | 0.3920 | 0.4182 | 0.4306 | 0.3839 |
| 3 | (7, 5) | 0.3884 | 0.4130 | 0.4299 | 0.3887 |
| 5 | (5, 5) | 0.4015 | 0.4257 | 0.4510 | 0.3955 |

Every row covers 50,000 shots per decoder.

**What it shows:**

- The order of the decoders is the same in all five experiments. Tensor-network contraction and belief matching have the lowest error rates (within about one percentage point of each other), correlated matching comes next, and PyMatching has the highest.
- All four decoders disagree with the actual observable flip on roughly 38% to 45% of shots, so they are only moderately better than guessing.
- Among the four distance-3 locations, the error rates differ by up to about three percentage points for the same decoder (for example 0.388 to 0.416 for belief matching).
- The distance-5 experiment does not show lower error rates than the distance-3 experiments (0.3955 to 0.4510 against 0.3839 to 0.4432). In this data, a larger distance is not associated with a lower decoder error rate.
- Only one distance-5 location is available, so the distance comparison rests on a single experiment.

## Q3: How does the repetition-code circuit map data qubits to parity-check ancillas, syndrome bits and conditional corrections?

**Queries:** `q3a_parity_checks.sql` and `q3b_conditional_corrections.sql`. Q3a joins `circuit`, `stabilizer_check` and `stabilizer_check_data_qubit` (three Gold tables). Q3b joins `conditional_correction`, `circuit` and `stabilizer_check` (also three tables). Only `qec_sm_n5` contains parity checks and corrections, so both queries return rows for this one benchmark.

**Q3a: parity checks**

| Variant | Check | Ancilla | Data qubits | Syndrome bit |
|---|---|---|---|---|
| source | `a[0]` | `a[0]` | `q[0]`, `q[1]` | `syn[0]` |
| source | `a[1]` | `a[1]` | `q[1]`, `q[2]` | `syn[1]` |
| transpiled | `a[0]` | `a[0]` | `q[0]`, `q[1]` | `syn[0]` |
| transpiled | `a[1]` | `a[1]` | `q[1]`, `q[2]` | `syn[1]` |

**Q3b: conditional corrections**

| Variant | Condition | Checks that fired | Gate | Target qubit |
|---|---|---|---|---|
| source | `syn == 1` | `a[0]` | `x` | `q[0]` |
| source | `syn == 2` | `a[1]` | `x` | `q[2]` |
| source | `syn == 3` | `a[0]`, `a[1]` | `x` | `q[1]` |
| transpiled | `syn == 1` | `a[0]` | `x` | `q[0]` |
| transpiled | `syn == 2` | `a[1]` | `x` | `q[2]` |
| transpiled | `syn == 3` | `a[0]`, `a[1]` | `x` | `q[1]` |

The "checks that fired" column is derived in SQL. The classical register `syn` is read as a binary number in which syndrome bit 0 is worth 1 and bit 1 is worth 2. A check fired when its bit is set in the condition value, which the query tests with `(condition_value >> syndrome_index) % 2 = 1`.

**What it shows:**

- This circuit is a repetition code on three data qubits (`q[0]`, `q[1]`, `q[2]`) with two ancilla qubits. The ancilla `a[0]` compares `q[0]` with `q[1]`, and `a[1]` compares `q[1]` with `q[2]`. Their measurement results are stored in `syn[0]` and `syn[1]`.
- Each correction matches the pattern of checks that fired. If only `a[0]` fires, `q[0]` is the only qubit that only that check reads, so the circuit flips `q[0]`. If only `a[1]` fires, it flips `q[2]`. If both fire, `q[1]` is the qubit both checks read, so it flips `q[1]`.
- The value `syn == 0` has no correction: no check fired, so there is nothing to repair.
- The source and transpiled variants return identical results, so transpiling did not change the logic.



# Limitations

*What goes here: Three or four honest points about where the design or the data stops being reliable.*

# Commands to run

*What goes here: The exact commands, in order: make bootstrap (repo root), then from starter/: make run (pipeline), make train (Part II models), make test (tests). Say what each does.*

# Troubleshooting

*What goes here: Common failures and fixes, and how the tests in starter/tests/ cover the tables (for example test_google_experiment.py).*

# AI usage

*What goes here: Where AI was used and where it was not, in a few sentences.*
