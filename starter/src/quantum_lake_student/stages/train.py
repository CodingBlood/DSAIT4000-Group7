"""Required Part II model-training stage.

Consume the two required ML input tables produced by Part I through the
supplied model-input and partition helpers. Publish repeatable model files,
predictions, metrics, run settings, the exact feature order, and the concise
Part II report under ``results/part2/``. Numerical performance is not graded.
"""
import hashlib
import json
import os
import pickle
import subprocess
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from sklearn.metrics import balanced_accuracy_score, brier_score_loss
from quantum_lake_student.ml import google_meta_model_input, syndrome_model_input
from quantum_lake_student.ml import unpack_little_endian_bits, GOOGLE_META_PREDICTION_COLUMNS
from quantum_lake_student.models import StageResult
from quantum_lake_student.ml import weighted_logical_error_rate
from quantum_lake_student.pipeline_utils import PipelineError, write_parquet
import time

SYNDROME_PATH = "ml/ml_syndrome_decoder_example.parquet"
GOOGLE_PATH = "ml/ml_google_decoder_example.parquet"
MANIFEST_PATH = "/course-data/metadata/bundle-manifest.json"
SEED = 7


def data_release_version() -> dict:
    if not os.path.exists(MANIFEST_PATH):
        raise PipelineError(f"Release manifest {MANIFEST_PATH} not found. Check that the course data is mounted.")
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)
    return {
        "release_name": manifest["release_name"],
        "release_date": manifest["release_date"],
        "bundle_version": manifest["bundle_version"],
    }


def get_feature_row(df: pd.DataFrame, i: int) -> tuple:
    row_dict = {}
    for column_name in df.columns:
        row_dict[column_name] = df[column_name][i]
    return google_meta_model_input(row_dict)


def build_features(df: pd.DataFrame) -> tuple[list, list]:
    features = []
    labels = []
    for i in range(len(df)):
        features.append(get_feature_row(df, i))
        labels.append(int(df["actual_observable_flip"][i]))
    return features, labels


def score_predictions(y, pred, prob) -> dict:
    result = {
        "logical_error_rate": weighted_logical_error_rate(y, pred, [1] * len(y)),
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "brier_score": None,
    }
    if prob is not None:
        result["brier_score"] = float(brier_score_loss(y, prob))
    return result


def add_prediction_rows(rows, df, model_id, y, pred, prob, split):
    for i in range(len(df)):
        probability = None
        if prob is not None:
            probability = float(prob[i])
        rows.append({
            "example_id": df["example_id"][i], "model_id": model_id,
            "label": bool(y[i]), "prediction": bool(pred[i]),
            "probability": probability, "split": split,
        })


def mistake_overlap(y_test, decoder_predictions: dict) -> dict:
    names = list(decoder_predictions)
    wrong = []
    for name in names:
        wrong.append(decoder_predictions[name] != y_test)
    wrong = np.column_stack(wrong)
    wrong_count = wrong.sum(axis=1)

    any_wrong = int((wrong_count > 0).sum())
    all_wrong = int((wrong_count == len(names)).sum())
    only_one_wrong = int((wrong_count == 1).sum())

    pairs = {}
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            both = int((wrong[:, a] & wrong[:, b]).sum())
            either = int((wrong[:, a] | wrong[:, b]).sum())
            pairs[names[a] + " & " + names[b]] = both / either if either > 0 else 0.0

    note = (
        f"{any_wrong} test shots were wrong for at least one decoder. "
        f"{all_wrong} of them were wrong for all four decoders and "
        f"{only_one_wrong} were wrong for only one decoder."
    )
    return {
        "shots_any_decoder_wrong": any_wrong,
        "shots_all_decoders_wrong": all_wrong,
        "shots_only_one_decoder_wrong": only_one_wrong,
        "pairwise_shared_mistake_share": pairs,
        "note": note,
    }


def run_task_b(df_ml: pd.DataFrame, distance: int) -> dict:
    train_df = df_ml[df_ml["data_split"] == "train"].reset_index(drop=True)
    val_df = df_ml[df_ml["data_split"] == "validation"].reset_index(drop=True)
    test_df = df_ml[df_ml["data_split"] == "test"].reset_index(drop=True)

    X_train, y_train = build_features(train_df)
    X_val, y_val = build_features(val_df)
    X_test, y_test = build_features(test_df)
    y_train = np.array(y_train)
    y_val = np.array(y_val)
    y_test = np.array(y_test)

    prefix = f"task_b_d{distance}_"
    predictions = []
    metrics = {}


    prior = float(y_train.mean())
    majority = int(prior >= 0.5)
    base_pred = np.full(len(y_test), majority)
    base_prob = np.full(len(y_test), prior)
    metrics[prefix + "majority_baseline"] = score_predictions(y_test, base_pred, base_prob)
    add_prediction_rows(predictions, test_df, prefix + "majority_baseline",
                        y_test, base_pred, base_prob, "test")


    decoder_predictions = {}
    for column in GOOGLE_META_PREDICTION_COLUMNS:
        pred = test_df[column].astype(int).to_numpy()
        decoder_predictions[column] = pred
        model_id = prefix + column.replace("_prediction", "")
        metrics[model_id] = score_predictions(y_test, pred, None)
        add_prediction_rows(predictions, test_df, model_id, y_test, pred, None, "test")


    model = LogisticRegression(max_iter=1000, random_state=SEED)
    fit_start = time.time()
    model.fit(X_train, y_train)
    training_time = time.time() - fit_start

    val_prob = model.predict_proba(X_val)[:, 1]
    best_threshold = 0.5
    best_score = -1.0
    for step in range(1, 20):
        threshold = step / 20
        val_pred = (val_prob >= threshold).astype(int)
        score = balanced_accuracy_score(y_val, val_pred)
        if score > best_score:
            best_score = score
            best_threshold = threshold

    predict_start = time.time()
    test_prob = model.predict_proba(X_test)[:, 1]
    prediction_time = time.time() - predict_start
    test_pred = (test_prob >= best_threshold).astype(int)

    combined_id = prefix + "combined_logistic_regression"
    metrics[combined_id] = score_predictions(y_test, test_pred, test_prob)
    metrics[combined_id]["threshold_from_validation"] = best_threshold
    metrics[combined_id]["validation_balanced_accuracy"] = float(best_score)
    metrics[combined_id]["training_time_seconds"] = training_time
    metrics[combined_id]["prediction_time_seconds"] = prediction_time
    metrics[combined_id]["train_rows"] = len(train_df)
    metrics[combined_id]["validation_rows"] = len(val_df)
    metrics[combined_id]["test_rows"] = len(test_df)
    add_prediction_rows(predictions, test_df, combined_id, y_test, test_pred, test_prob, "test")

    overlap = mistake_overlap(y_test, decoder_predictions)
    return {
        "predictions": predictions, "metrics": metrics,
        "overlap": overlap, "model_id": combined_id, "model": model,
    }


def run_task_c(df_google: pd.DataFrame) -> dict:

    df = df_google[(df_google["distance"] == 3) & (df_google["shot_index"] < 12500)]
    df = df.reset_index(drop=True)

    X = []
    for packed in df["detector_bits"]:
        X.append(unpack_little_endian_bits(bytes(packed), 200))
    X = np.array(X)
    y = df["actual_observable_flip"].astype(int).to_numpy()
    splits = df["data_split"].to_numpy()

    model = MLPClassifier(hidden_layer_sizes=(32,), max_iter=200, random_state=SEED)
    train = splits == "train"
    fit_start = time.time()
    model.fit(X[train], y[train])
    training_time = time.time() - fit_start

    predictions = []
    metrics = {}
    for split in ("validation", "test"):
        mask = splits == split
        predict_start = time.time()
        prob = model.predict_proba(X[mask])[:, 1]
        prediction_time = time.time() - predict_start
        pred = (prob >= 0.5).astype(int)
        metrics[split] = score_predictions(y[mask], pred, prob)
        metrics[split]["training_time_seconds"] = training_time
        metrics[split]["prediction_time_seconds"] = prediction_time
        metrics[split]["rows"] = int(mask.sum())
        split_df = df[mask].reset_index(drop=True)
        add_prediction_rows(predictions, split_df, "task_c_mlp", y[mask], pred, prob, split)
    return {"predictions": predictions, "metrics": metrics, "model": model}


class WeightedPriorBaseline:

 
    def fit(self, X, y, sample_weight):
        y = np.asarray(y)
        w = np.asarray(sample_weight, dtype=float)
        self.prior_ = float(w[y == 1].sum() / w.sum())
        return self
 
    def predict_proba(self, X):
        p1 = np.full(len(X), self.prior_)
        return np.column_stack([1 - p1, p1])
 
 
def run_task_a(df_syn: pd.DataFrame) -> dict:
    X = np.array([syndrome_model_input(b) for b in df_syn["syndrome_bits"]])
    y = df_syn["logical_error_label"].astype(int).to_numpy()
    w = df_syn["sample_weight"].to_numpy().astype(float)
    ids = df_syn["example_id"].to_numpy()
    splits = df_syn["data_split"].to_numpy()
 
    models = {
        "task_a_weighted_prior_baseline": WeightedPriorBaseline(),
        "task_a_weighted_logistic_regression": LogisticRegression(max_iter=1000, random_state=SEED),
    }
    train = splits == "train"
    predictions, metrics, fitted = [], {}, {}
    for model_id, model in models.items():
        fit_start = time.time()
        model.fit(X[train], y[train], sample_weight=w[train])
        training_time = time.time() - fit_start
        fitted[model_id] = model
        metrics[model_id] = {}
        for split in ("validation", "test"):
            mask = splits == split
            predict_start = time.time()
            prob = model.predict_proba(X[mask])[:, 1]
            prediction_time = time.time() - predict_start
            pred = (prob >= 0.5).astype(int)
            metrics[model_id][split] = {
                "logical_error_rate": weighted_logical_error_rate(y[mask], pred, w[mask]),
                "balanced_accuracy": balanced_accuracy_score(y[mask], pred, sample_weight=w[mask]),
                "brier_score": brier_score_loss(y[mask], prob, sample_weight=w[mask]),
                "training_time_seconds": training_time,
                "prediction_time_seconds": prediction_time,
                "rows": int(mask.sum()),
            }
            for k, i in enumerate(np.where(mask)[0]):
                predictions.append({
                    "example_id": ids[i], "model_id": model_id, "label": bool(y[i]),
                    "prediction": bool(pred[k]), "probability": float(prob[k]),
                    "split": split,
                })
    return {"predictions": predictions, "metrics": metrics, "models": fitted}


def run(model_run_id: str) -> StageResult:
    
    result = StageResult(stage="train", run_id=model_run_id)
    run_start = time.time()

    os.makedirs("results/part2/models", exist_ok=True)

    for path in [GOOGLE_PATH, SYNDROME_PATH]:
        if not os.path.exists(path):
            raise PipelineError(f"ML table {path} not found. Run make run before make train.")
    df_google_ml = pd.read_parquet(GOOGLE_PATH)
    df_syndrome_ml = pd.read_parquet(SYNDROME_PATH)

    task_a_result = run_task_a(df_syndrome_ml)
    task_b_results = {}
    for distance in (3, 5):
        df_distance = df_google_ml[df_google_ml["distance"] == distance].reset_index(drop=True)
        task_b_results[distance] = run_task_b(df_distance, distance)
    task_c_result = run_task_c(df_google_ml)

    all_predictions = task_a_result["predictions"]
    for distance in (3, 5):
        all_predictions = all_predictions + task_b_results[distance]["predictions"]
    all_predictions = all_predictions + task_c_result["predictions"]
    df_predictions = pd.DataFrame(all_predictions)
    write_parquet(df_predictions, "results/part2/predictions.parquet")

    with open("results/part2/metrics.json", "w") as f:
        json.dump({
            "task_a": task_a_result["metrics"],
            "task_b": {
                "distance_3": {"models": task_b_results[3]["metrics"], "mistake_overlap": task_b_results[3]["overlap"]},
                "distance_5": {"models": task_b_results[5]["metrics"], "mistake_overlap": task_b_results[5]["overlap"]},
            },
            "task_c": task_c_result["metrics"],
        }, f, indent=2)
 
    all_models = dict(task_a_result["models"])
    for distance in (3, 5):
        all_models[task_b_results[distance]["model_id"]] = task_b_results[distance]["model"]
    all_models["task_c_mlp"] = task_c_result["model"]
    for name, model in all_models.items():
        with open(f"results/part2/models/{name}.pkl", "wb") as f:
            pickle.dump(model, f)
 
    def file_hash(path):
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
 
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        revision = "unknown"
    import importlib.metadata as md

    timings = {"task_a": {}, "task_b": {}}
    for model_id in task_a_result["metrics"]:
        test_metrics = task_a_result["metrics"][model_id]["test"]
        timings["task_a"][model_id] = {
            "training_time_seconds": test_metrics["training_time_seconds"],
            "prediction_time_seconds": test_metrics["prediction_time_seconds"],
        }
    for distance in (3, 5):
        model_id = task_b_results[distance]["model_id"]
        combined_metrics = task_b_results[distance]["metrics"][model_id]
        timings["task_b"][model_id] = {
            "training_time_seconds": combined_metrics["training_time_seconds"],
            "prediction_time_seconds": combined_metrics["prediction_time_seconds"],
        }
    timings["task_c"] = {
        "task_c_mlp": {
            "training_time_seconds": task_c_result["metrics"]["test"]["training_time_seconds"],
            "prediction_time_seconds": task_c_result["metrics"]["test"]["prediction_time_seconds"],
        }
    }
    timings["total_run_seconds"] = time.time() - run_start

    run_record = {
        "model_run_id": model_run_id,
        "data_release_version": data_release_version(),
        "input_table_hashes": {
            "ml_syndrome_decoder_example": file_hash(SYNDROME_PATH),
            "ml_google_decoder_example": file_hash(GOOGLE_PATH),
        },
        "code_revision": revision,
        "dependency_versions": {p: md.version(p) for p in ("scikit-learn", "pandas", "numpy", "pyarrow")},
        "feature_order": {
            "task_a": [f"syndrome_bit_{i}" for i in range(16)],
            "task_b": ["detector_event_density", "belief_matching_prediction",
                       "correlated_matching_prediction", "pymatching_prediction",
                       "tensor_network_contraction_prediction"],
            "task_c": [f"detector_bit_{i}" for i in range(200)],
        },
        "split_rule": {
            "task_a": "syndrome_data_split: 0.0005 validation, 0.005 test, rest train",
            "task_b": "google_data_split, run separately for distance 3 and distance 5: odd shot_index test, even with %10==8 validation, rest train",
            "task_c": "google_data_split on rows with distance=3 AND shot_index<12500",
        },
        "random_seed": SEED,
        "timings": timings,
    }
    with open("results/part2/run.json", "w") as f:
        json.dump(run_record, f, indent=2)
 
    result.input_count = len(df_syndrome_ml) + len(df_google_ml)
    result.output_count = len(df_predictions)
    result.issue_count = 0
    result.finish()
    return result