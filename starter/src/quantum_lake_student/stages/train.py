"""Required Part II model-training stage.

Consume the two required ML input tables produced by Part I through the
supplied model-input and partition helpers. Publish repeatable model files,
predictions, metrics, run settings, the exact feature order, and the concise
Part II report under ``results/part2/``. Numerical performance is not graded.
"""

import json
import os
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, brier_score_loss
from quantum_lake_student.ml import google_meta_model_input
from quantum_lake_student.models import StageResult
from quantum_lake_student.ml import weighted_logical_error_rate
import time


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
    return {"predictions": predictions, "metrics": metrics}



def run(model_run_id: str) -> StageResult:
    # raise NotImplementedError("Implement the documented QEC decoder training stage")
    result = StageResult(stage="train", run_id=model_run_id)

    os.makedirs("results/part2", exist_ok=True)

    df_google_ml = pd.read_parquet("results/part2/ml/ml_google_decoder_example.parquet")
    task_b_result = run_task_b(df_google_ml)

    df_predictions = pd.DataFrame(task_b_result["predictions"])
    df_predictions.to_parquet("results/part2/predictions.parquet", index=False)

    with open("results/part2/metrics.json", "w") as f:
        json.dump({"task_b": task_b_result["metrics"]}, f, indent=2)
    
    result.input_count = len(df_google_ml)
    result.output_count = len(df_predictions)
    result.issue_count = 0
    result.finish()
    return result