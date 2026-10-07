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

*What goes here: One table of the three sources: name, Bronze file, size, content. Keep it to one line per source.*

| **Source**          | **Bronze file**                 | **Size**   | **Content**                                      |
|---------------------|---------------------------------|------------|--------------------------------------------------|
| Google surface code | google-surface-code-curated.zip | 14,638,673 | Experiment detector data and decoder predictions |
| QEC syndromes       | syndromes_dataset.zip           | 358,017    | Syndrome histograms at 7 fault rates             |
| QASMBench           | qasmbench-qec.zip               | 144,172    | 6 circuit files plus a gate library              |

## **Google surface-code source**

*What goes here: What the source contains (5 experiments, 250,000 shots, four decoders), how the bits are stored (Stim b8 format), and the problem we found.*

## **Syndrome source**

*What goes here: What the source contains (7 fault rates, 10M observations each stored as weighted counts), what one row means, and the problem we found (row count is not observation count).*

## **QASMBench source**

- There are 3 benchmarks, each in two variants (source and transpiled), giving 6 circuit files. There is also qelib1.inc, which is the standard gate library and not a circuit.

- The benchmarks are error_correctiond3_n5, qec_en_n5 and qec_sm_n5. All have 5 qubits.

- Only qec_sm_n5 contains classical feedback. It has 3 data qubits, 2 ancilla qubits, a 2-bit syndrome register and 3 conditional X corrections.

- **Problem we found :** the text contains gate definitions (gate syndrome {...}). A definition is not an operation, so counting it would double-count the gates.

# Architecture

*What goes here: How data moves from the raw files to the ML tables, and which code does each step (checklist: architecture and data flow).*

## Pipeline and data flow

*What goes here: The diagram (course data, Bronze, Silver, Gold, ML tables, with the technique on each arrow) plus one sentence per stage.*

## Stages, key functions and techniques

*What goes here: One table: stage, key functions, processing techniques (for example checksum verification, bit unpacking, macro expansion, bulk COPY in one transaction). Include only functions that carry a decision.*

# Silver: cleaning, validation, traceability

*What goes here: Show that bad data is handled by clear rules and every row can be traced to its source.*

## Silver tables

*What goes here: The six tables, their source, what one row means and the row counts.*

## Cleaning and validation rules

*What goes here: Reject (error) versus warn (warning), with a few example rules per source. Full rule lists can go in an appendix.*

## Traceability

*What goes here: The source_record_id formats, source_trace.parquet, data_issues.parquet and how a row is followed back to its Bronze file.*

# Gold schema and key decisions

*What goes here: The 13-table PostgreSQL model and the reasons behind it (checklist: ER diagram of the Gold tables, Gold design and key decisions).*

## Gold design by source

### Google

*What goes here: Experiment, shot, decoder and shot_prediction, and why predictions have their own table.*

### Syndrome

*What goes here: Experiment, pattern and observation, and why patterns are stored once with the count (weight) on the observation.*

### QASMBench

*What goes here: Circuit, register, qubit, stabilizer check, check-to-data-qubit junction and conditional correction; why the circuit text is split into registers and qubits.*

## ER diagram of the Gold tables

*What goes here: One diagram per source group (or one combined), with primary and foreign keys shown.*

## Key decisions

*What goes here: A table: decision, reason, alternative not chosen. Include the transaction load, the deterministic ids and the ML views.*

# The rejected relationship

*What goes here: The join between QASMBench and the experiment sources that we tested and rejected: why (circuits versus outputs), the result table from rejected_join_qasmbench.sql (starter/sql/analysis/), and the decision.*

# Data-quality findings

*What goes here: What the checks found, honestly (checklist: quality findings).*

## Row counts

*What goes here: Read, accepted and rejected rows per table from results/part1/row_counts.json, and the total.*

## Warnings and rejected rows

*What goes here: What is in data_issues.parquet (80 warnings: the sx gate and the if keyword) and what they mean.*

## Known gaps

*What goes here: Anything not clean, for example the all-zero-syndrome logical errors and the decoder error rates.*

# Worked traces

*What goes here: Proof that an ML example can be followed back to its source (checklist: the example traces). Use trace_examples.json.*

## Google shot trace

*What goes here: ML row, Gold, Silver, Bronze, with the keys used at each step.*

## Syndrome trace

*What goes here: The same chain for one syndrome row, ending with its Part II predictions.*

# SQL analyses

*What goes here: The three required analyses over the Gold tables; at least one must join three or more tables. Outputs are in results/part1/analysis/.*

## Q1: Syndromes by physical fault rate

*What goes here: Question, tables joined, small result table, two-line interpretation.*

## Q2: Decoder error rates

*What goes here: Question, tables joined, result table, interpretation.*

## Q3: Repetition-code circuit mapping

*What goes here: Question, the three-or-more-table join, the checks and corrections tables, interpretation.*

# Limitations

*What goes here: Three or four honest points about where the design or the data stops being reliable.*

# Commands to run

*What goes here: The exact commands, in order: make bootstrap (repo root), then from starter/: make run (pipeline), make train (Part II models), make test (tests). Say what each does.*

# Troubleshooting

*What goes here: Common failures and fixes, and how the tests in starter/tests/ cover the tables (for example test_google_experiment.py).*

# AI usage

*What goes here: Where AI was used and where it was not, in a few sentences.*
