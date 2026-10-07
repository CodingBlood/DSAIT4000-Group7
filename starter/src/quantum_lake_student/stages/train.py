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
from sklearn.metrics import accuracy_score, balanced_accuracy_score, brier_score_loss
from quantum_lake_student.ml import google_meta_model_input, syndrome_model_input
from quantum_lake_student.models import StageResult
from quantum_lake_student.ml import weighted_logical_error_rate
import time

SYNDROME_PATH = "results/part2/ml/ml_syndrome_decoder_example.parquet"
GOOGLE_PATH = "results/part2/ml/ml_google_decoder_example.parquet"
SEED = 7
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


def run_task_b(df_ml: pd.DataFrame) -> dict:
    train_df = df_ml[df_ml["data_split"] == "train"].reset_index(drop=True)
    test_df = df_ml[df_ml["data_split"] == "test"].reset_index(drop=True)

    X_train, y_train = build_features(train_df)
    X_test, y_test = build_features(test_df)

    model = LogisticRegression(max_iter=1000)

    fit_start = time.time()
    model.fit(X_train, y_train)
    training_time = time.time() - fit_start

    predict_start = time.time()
    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)[:, 1]
    prediction_time = time.time() - predict_start

    predictions = []
    for i in range(len(test_df)):
        predictions.append({
            "example_id": test_df["example_id"][i],
            "predicted_flip": bool(y_pred[i]),
            "predicted_probability": float(y_prob[i]),
        })

    metrics = {
        "logical_error_rate": weighted_logical_error_rate(y_test, y_pred, [1] * len(y_test)),
        "balanced_accuracy": balanced_accuracy_score(y_test, y_pred),
        "brier_score": brier_score_loss(y_test, y_prob),
        "training_time_seconds": training_time,
        "prediction_time_seconds": prediction_time,
        "train_rows": len(train_df),
        "test_rows": len(test_df),
    }
    return {"predictions": predictions, "metrics": metrics, "model": model}

class WeightedPriorBaseline:
    """Weighted majority/prior baseline: predicts the weighted positive rate."""
 
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
    # raise NotImplementedError("Implement the documented QEC decoder training stage")
    result = StageResult(stage="train", run_id=model_run_id)

    os.makedirs("results/part2/models", exist_ok=True)

    df_google_ml = pd.read_parquet("results/part2/ml/ml_google_decoder_example.parquet")
    df_syndrome_ml = pd.read_parquet("results/part2/ml/ml_syndrome_decoder_example.parquet")

    task_a_result = run_task_a(df_syndrome_ml)
    task_b_result = run_task_b(df_google_ml)

    labels = df_google_ml.set_index("example_id")["actual_observable_flip"]
    task_b_rows = [
        {
            "example_id": p["example_id"],
            "model_id": "task_b_combined_logistic_regression",
            "label": bool(labels[p["example_id"]]),
            "prediction": p["predicted_flip"],
            "probability": p["predicted_probability"],
            "split": "test",
        }
        for p in task_b_result["predictions"]
    ]
    df_predictions = pd.DataFrame(task_a_result["predictions"] + task_b_rows)
    df_predictions.to_parquet("results/part2/predictions.parquet", index=False)

    with open("results/part2/metrics.json", "w") as f:
        json.dump({"task_a": task_a_result["metrics"], "task_b": task_b_result["metrics"]}, f, indent=2)
 
    for name, model in {**task_a_result["models"], "task_b_combined_logistic_regression": task_b_result["model"]}.items():
        with open(f"results/part2/models/{name}.pkl", "wb") as f:
            pickle.dump(model, f)
 
    def file_hash(path):
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
 
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        revision = "unknown"
    import importlib.metadata as md
    run_record = {
        "model_run_id": model_run_id,
        "data_release_version": os.environ.get("DATA_RELEASE_VERSION", "unknown"),
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
        },
        "split_rule": {
            "task_a": "syndrome_data_split: 0.0005 validation, 0.005 test, rest train",
            "task_b": "google_data_split: odd shot_index test, even with %10==8 validation, rest train",
        },
        "random_seed": SEED,
    }
    with open("results/part2/run.json", "w") as f:
        json.dump(run_record, f, indent=2)
 
    result.input_count = len(df_syndrome_ml) + len(df_google_ml)
    result.output_count = len(df_predictions)
    result.issue_count = 0
    result.finish()
    return result