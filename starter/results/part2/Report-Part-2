# Group – 7 – DME PROJECT – PART II

# Overview

In Part II we use the two ML tables from Part I to train and compare some simple models. The goal is not a high score. The goal is to show that the data from Part I can be used by a model in a correct and repeatable way. `train.py` reads only the two Parquet tables in `ml/`. It does not read Gold, Silver or the raw zip files.

We worked on three tasks:

1.  **Task A: weighted syndrome decoder.** Predict the logical-error label from the 16 syndrome bits, using the physical weights.

2.  **Task B: Google decoders.** Compare the four supplied decoders with a simple combined decoder, for distance 3 and distance 5 separately.

3.  **Task C: raw-detector prototype.** Train a small neural network (MLP) on the 200 raw detector bits of the distance-3 shots.

All results come from `results/part2/metrics.json` and `results/part2/run.json`, created by one run of `make train` (seed 7, scikit-learn 1.9.0). All numbers are from the supplied splits, and the test data was only used for the final numbers.

# Inputs and targets

|           **Table**           | **Rows** |                                            **One row is**                                             |        **Target**        |                                          **Split**                                         |
|-------------------------------|----------|-------------------------------------------------------------------------------------------------------|--------------------------|--------------------------------------------------------------------------------------------|
| `ml_syndrome_decoder_example` |  75,598  | one distinct syndrome and label in one fault-rate file. It stands for `sample_weight` simulated shots | `logical_error_label`    | by fault rate: 0.0005 is validation, 0.005 is test, the other five are train               |
|  `ml_google_decoder_example`  |  250,000 | one hardware shot                                                                                     | `actual_observable_flip` | by `shot_index`: odd is test, even with `index % 10 == 8` is validation, the rest is train |

Number of rows per split:

|               **Task**                | **Train** | **Validation** | **Test** |
|---------------------------------------|-----------|----------------|----------|
| A (syndrome)                          | 53,304    | 1,407          | 20,887   |
| B, distance 3                         | 80,000    | 20,000         | 100,000  |
| B, distance 5                         | 20,000    | 5,000          | 25,000   |
| C (distance 3, `shot_index` < 12,500) | 20,000    | 5,000          | 25,000   |

We used the supplied splits as they are and did not move any example from one split to another.

# Task A: weighted syndrome decoder

## Models

- **Weighted prior baseline.** It predicts the weighted share of logical errors in the training rows. This share is 0.0426, which is below 0.5, so the baseline always predicts "no logical error".
- **Weighted logistic regression.** One weight for each of the 16 syndrome bits, fitted with `sample_weight`.

We used `sample_weight` (the source `quantity`) for fitting and for every metric. We did not expand the 70 million observations into separate rows.

## Results

|           **Model**          | **Split**  | **Logical-error rate** | **Balanced accuracy** | **Brier score** | **Train time (s)** | **Predict time (s)** |
|------------------------------|------------|------------------------|-----------------------|-----------------|--------------------|----------------------|
| Weighted prior               | validation | 0.01144                | 0.500                 | 0.01228         | 0.006              | 0.0003               |
| Weighted prior               | test       | 0.10395                | 0.500                 | 0.09691         | 0.006              | 0.0015               |
| Weighted logistic regression | validation | 0.01151                | 0.502                 | 0.01064         | 0.616              | 0.0006               |
| Weighted logistic regression | test       | 0.10945                | 0.522                 | 0.09096         | 0.616              | 0.0019               |

**What it shows:**

- The data is very imbalanced. The weighted logical-error rate goes from 0.0002 at fault rate 1e-5 to 0.19 at 1e-2 (Q1 in Part I). In the validation file about 94% of the weighted observations have an all-zero syndrome.
- Because of this, the baseline's error rate on test (0.10395) is just the share of logical errors in the test file. It detects nothing, which is why its balanced accuracy is exactly 0.5.
- The logistic regression did **not** improve the main metric. On test its error rate is 0.1094 against 0.1040 for the baseline, and on validation the two are almost equal.
- It does a little better on the other two metrics. Its Brier score on test is 0.0910 against 0.0969, and its balanced accuracy is 0.522. So the 16 bits carry some information, but a simple weighted sum of the bits does not turn it into better predictions.
- We did not tune a threshold for Task A. All predictions use 0.5.

## What the logistic regression learned

The bit number is `4 × round + check`. The weights of the fitted model (intercept -4.03) are:

| **Round** | **check 0** | **check 1** | **check 2** | **check 3** |
|-----------|-------------|-------------|-------------|-------------|
| 0         | 1.73        | -0.16       | 2.29        | -0.02       |
| 1         | 0.74        | 0.38        | 1.06        | 0.41        |
| 2         | 0.89        | 0.28        | 1.28        | 0.29        |
| 3         | 1.56        | 0.12        | 1.50        | 0.18        |

Checks 0 and 2 have the largest weight in every round, and checks 1 and 3 have much smaller weights (between -0.16 and 0.41). A fired check makes a logical error more likely, and the large negative intercept keeps the default prediction low. A model that adds up weights cannot use combinations of bits (for example "check 0 and check 2 fire together"). This may be the reason it does not beat the baseline, but we did not test this.

**Limitation.** Validation (fault rate 0.0005) and test (0.005) are very different noise levels. The validation score therefore says little about the test score. For example, the baseline's error rate is 0.011 on validation and 0.104 on test.

# Task B: supplied and combined Google decoders

We did distance 3 and distance 5 separately, because their detector vectors have different widths (200 and 600). For each distance, the baseline, the four supplied decoders and the combined model are scored on the **same test shots**. The supplied decoders only give a 0/1 prediction, so their Brier score is not applicable.

**Combined model.** A logistic regression on five inputs, in this order: detector-event density, belief matching, correlated matching, PyMatching and tensor-network contraction. We chose the threshold on validation only (0.50 for distance 3 and 0.55 for distance 5).

## Results, distance 3 (100,000 test shots)

|           **Model**          | **Logical-error rate** | **Balanced accuracy** | **Brier score** |
|------------------------------|------------------------|-----------------------|-----------------|
| Majority baseline            | 0.4930                 | 0.500                 | 0.2500          |
| Belief matching              | 0.4013                 | 0.599                 | n/a             |
| Correlated matching          | 0.4220                 | 0.578                 | n/a             |
| PyMatching                   | 0.4364                 | 0.563                 | n/a             |
| Tensor-network contraction   | **0.3967**             | 0.603                 | n/a             |
| Combined logistic regression | 0.3972                 | 0.603                 | 0.2352          |

## Results, distance 5 (25,000 test shots)

|          **Model**           | **Logical-error rate** | **Balanced accuracy** | **Brier score** |
|------------------------------|------------------------|-----------------------|-----------------|
| Majority baseline            | 0.5015                 | 0.500                 | 0.2500          |
| Belief matching              | 0.3994                 | 0.601                 | n/a             |
| Correlated matching          | 0.4226                 | 0.577                 | n/a             |
| PyMatching                   | 0.4548                 | 0.545                 | n/a             |
| Tensor-network contraction   | **0.3952**             | 0.605                 | n/a             |
| Combined logistic regression | 0.3977                 | 0.603                 | 0.2341          | 

Training the combined model took 0.054 s (distance 3) and 0.015 s (distance 5). Prediction took 0.028 s and 0.006 s.

**What it shows:**

- All four decoders are better than the majority baseline, but only by about 0.05 to 0.11 in error rate. None of them is close to reliable on these shots.
- Tensor-network contraction and belief matching are the best, and PyMatching is the worst, at both distances.
- The combined model is as good as the best single decoder, but not better. It scores 0.3972 against 0.3967 at distance 3, and 0.3977 against 0.3952 at distance 5. These differences are small compared with the sampling noise of the test sets (about 0.0015 for 100,000 shots and 0.003 for 25,000 shots), so we do not claim that one is better than the other.
- Distance 5 error rates are about the same as distance 3 (0.395 to 0.455 against 0.397 to 0.436). These are different test sets, so we do not read more into this.

## Do the decoders make the same mistakes?

|                                                |      **Distance 3**     |     **Distance 5**     |
|------------------------------------------------|-------------------------|------------------------|
| Test shots wrong for at least one decoder      | 69,824 (69.8%)          | 18,908 (75.6%)         |
| Wrong for all four decoders                    | 14,440 (20.7% of those) | 2,713 (14.3% of those) |
| Wrong for exactly one decoder                  | 19,031 (27.3% of those) | 5,933 (31.4% of those) |
| Shared-mistake share for each pair of decoders | 0.36 to 0.53            | 0.33 to 0.49           |

**What it shows:**

- The decoders do not make the same mistakes. About a quarter to a third of the shots that are wrong for some decoder are wrong for only one of them.
- But they also overlap a lot. A large group of shots is wrong for all four decoders, and no vote can fix those.
- At distance 5, belief matching and tensor-network contraction agree the most (0.49). At distance 3, the two pairs that agree the most are belief matching with tensor-network contraction (0.53) and correlated matching with PyMatching (0.53). PyMatching and tensor-network contraction agree the least at both distances (0.36 and 0.33).
- The combined model only sees the event density and four 0/1 votes. So the best it can learn is a weighted vote. This helps when one decoder is wrong and the others are right, but not when all four are wrong. It also cannot know which shots a decoder usually gets wrong. This fits the result that it ends up close to the best single decoder and not above it.

## What the combined models learned

|            | **density** | **belief** | **correlated** | **PyMatching** | **tensor network** | **intercept** |
|------------|-------------|------------|----------------|----------------|--------------------|---------------|
| distance 3 | 0.38        | 0.38       | 0.29           | 0.17           | 0.53               | -0.76         |
| distance 5 | 1.10        | 0.38       | 0.26           | 0.22           | 0.59               | -0.90         |

At both distances the tensor-network vote has the largest weight and PyMatching the smallest. This is the same order as their error rates, so the model learned to trust the better decoders more. The density weight is about three times larger at distance 5.

# Task C: raw-detector prototype

## Model

One `MLPClassifier` with one hidden layer of 32 units (200 → 32 → 1, ReLU, Adam, 6,465 parameters), `max_iter = 200` and seed 7. The input is the 200 detector bits, unpacked with the little-endian bit order. We only used rows with `distance = 3` and `shot_index < 12,500` (50,000 rows) and the supplied split. We did not tune any setting.

## Results

| **Split**  | **Logical-error rate** | **Balanced accuracy** | **Brier score** |
|------------|------------------------|-----------------------|-----------------|
| Validation | 0.4902                 | 0.510                 | 0.3804          |
| Test       | 0.5066                 | 0.493                 | 0.3899          |

Training took 5.46 s and prediction on the test split took 0.081 s.

**What it shows:**

- The model does not learn anything useful. The error rate and balanced accuracy are at chance level.
- The Brier score of 0.39 is worse than the 0.25 of a model that always says 50%. So the network is confidently wrong.
- The training loss is 0.385, far below the 0.693 of a constant guess. So it fitted the 20,000 training shots, but this did not carry over to new shots.
- It used all 200 iterations, so it stopped at the limit and not because it had converged.
- We report this as a negative result. It shows that the packed bits can be delivered to a model in the right form. It does not show that the raw bits contain no information.

## What a flat vector of 200 bits does not show

- **Position.** The network does not know where a detector is on the chip. Two neighbouring detectors look as unrelated as two detectors far apart.
- **Neighbourhood.** A logical error comes from chains of events that are connected in space. A flat vector has no information about which detectors are next to each other.
- **Time.** The 200 bits cover 25 rounds, but the vector does not say which bit belongs to which round. An event that shows up in round 3 and again in round 4 is not visible as such.
- **Location of the experiment.** The four distance-3 chip locations are mixed together, and the input does not say which one a shot comes from.

A fully connected network has to find all of this by itself from 20,000 shots, which is little for 200 inputs. The matching decoders use exactly this structure, which may be why they do better.

# Information left out of each model

- **Task A:** `experiment_id`, `physical_fault_rate` (it defines the split, so it cannot be a feature) and the constant `round_count` and `check_count`. The 16 bits are used as separate inputs, so only their order carries the round structure.
- **Task B:** the raw detector, measurement and sweep bits are not used. The event count becomes a single density, and the four predictions are 0/1 values with no confidence.
- **Task C:** the position of the shot in its experiment, the number of rounds, the location and the supplied decoder predictions are not inputs.

# Saved models

The fitted models are in `results/part2/models/`.

|                   **File**                   |                                       **What it is**                                        |
|----------------------------------------------|---------------------------------------------------------------------------------------------|
| `task_a_weighted_prior_baseline.pkl`         | `WeightedPriorBaseline` (our own class in `train.py`). It has one value, `prior_ = 0.0426`. |
| `task_a_weighted_logistic_regression.pkl`    | `LogisticRegression`, 16 weights and intercept -4.03, fitted with `sample_weight`.          |
| `task_b_d3_combined_logistic_regression.pkl` | `LogisticRegression`, 5 weights and intercept -0.76, distance 3 only.                       |
| `task_b_d5_combined_logistic_regression.pkl` | `LogisticRegression`, 5 weights and intercept -0.90, distance 5 only.                       |
| `task_c_mlp.pkl`                             | `MLPClassifier`, 200 → 32 → 1.                                                              |

Two things to know about this folder:

- `task_b_combined_logistic_regression.pkl` is in the folder but has no entry in `metrics.json`, and its weights differ from the d3 and d5 files. It looks like a leftover from an earlier run that mixed both distances. It is not used in this report.
- The pickle files depend on the library version. They were saved with scikit-learn 1.9.0 (see `run.json`) and give a warning with other versions. The baseline file also needs the `quantum_lake_student` package to load.

# Repeatability and tracing

`make train` creates the models, `predictions.parquet`, `metrics.json` and `run.json`. The file `run.json` records the release version, the SHA-256 hashes of both ML tables, the code revision, the library versions, the feature order, the split rules, the seed and the timings. Every row in `predictions.parquet` keeps its `example_id`, so a prediction can be followed back through Gold and Silver to the source files (see `results/part1/trace_examples.json`).

# Limitations

- In Task A, validation and test have very different noise levels (0.0005 and 0.005), so the validation score is not a good guide for the test score. The logistic regression is also not better than the baseline on the main metric.
- In Task B, the differences between the combined model and the best single decoder are within the sampling noise. We do not claim a ranking between them.
- Task C is a small prototype with fixed settings and no tuning. Its failure says nothing about better-designed models.
- Training times come from one run on one machine and are not averaged.
- The model files only load with the same library version that created them.