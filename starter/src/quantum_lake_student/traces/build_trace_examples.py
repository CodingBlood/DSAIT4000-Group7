import json
import os

from quantum_lake_student.config import Settings
from quantum_lake_student.connections import postgres_connection

import pandas as pd

SHOT_ID = "google_shot_81ebcd9698dc" # shot table example

def build_google_trace():

    # DEMONSTRATE THE LINK BETWEEN GOOGLE SHOT AND GOOGLE EXPERIEMT IN SILVER 

    # grab the tuple from google_shot table
    df_shots = pd.read_parquet("silver/google_qec/shots.parquet")
    shot_row = df_shots[df_shots["source_record_id"] == SHOT_ID].iloc[0] 

    # grab the tuple from Google experiments table
    df_exp = pd.read_parquet("silver/google_qec/experiment.parquet")
    exp_row = df_exp[df_exp["experiment_id"] == shot_row["experiment_id"]].iloc[0]

    # grab the tuple from Shared tracing rule table 
    df_trace = pd.read_parquet("results/part1/source_trace.parquet")
    trace_rows = df_trace[df_trace["source_record_id"] == SHOT_ID]

    # NOW TAKE THE TUPLE FROM THE GOLD RELATION
    settings = Settings.from_environment()
    with postgres_connection(settings) as conn: # open Postgres connection 
        with conn.cursor() as cur:
            # select gold table shot through shot_id
            cur.execute( 
                """
                SELECT shot_id, experiment_id, shot_index, detector_event_count,
                       actual_observable_flip
                FROM shot WHERE shot_id = %s
                """,
                (SHOT_ID,),
            )
            gold_shot = cur.fetchone()

            # get all 4 decoder predicition
            cur.execute(
                """
                SELECT decoder_id, predicted_flip
                FROM shot_prediction WHERE shot_id = %s
                ORDER BY decoder_id
                """,
                (SHOT_ID,),
            )
            gold_predictions = cur.fetchall() 


    # RETURN THE FULL TRACE
    return {
        "example_type": "google_qec_shot",
        "source_record_id": SHOT_ID,
        "gold": {
            "table": "shot",
            "row": {
                "shot_id": gold_shot[0],
                "experiment_id": gold_shot[1],
                "shot_index": gold_shot[2],
                "detector_event_count": gold_shot[3],
                "actual_observable_flip": gold_shot[4],
            },
            "shot_prediction_rows": [
                {"decoder_id": r[0], "predicted_flip": r[1]}
                for r in gold_predictions
            ],
        },
        "silver" :  {
            "shot_table": "silver/google_qec/shots/parquet",
            "shot_row": {
                "source_record_id": shot_row["source_record_id"],
                "experiment_id": shot_row["experiment_id"],
                "shot_index": int(shot_row["shot_index"]),
                "detector_event_count": int(shot_row["detector_event_count"]),
                "actual_observable_flip": bool(shot_row["actual_observable_flip"]),
            },
            "experiment_table": "silver/google_qec/experiment.parquet",
            "experiment_row" : {
                "experiment_id": exp_row["experiment_id"],
                "basis": exp_row["basis"],
                "distance": int(exp_row["distance"]),
                "rounds": int(exp_row["rounds"]),
                "center_row": int(exp_row["center_row"]),
                "center_col": int(exp_row["center_col"]),
            },
        },
        "bronze": {
            "source_trace_rows": trace_rows.to_dict(orient="records")
        },
    }

def main():
    google_trace = build_google_trace()

    # TODO : syndrome_trace

    record = {
        "google_trace": google_trace,
        "syndrome_trace": None # syndrome_trace placeholder    
    }

    os.makedirs("results/part1", exist_ok=True)
    with open("results/part1/trace_examples.json", "w") as f:
        json.dump(record, f, indent=2, default=str)
    print("Wrote results/part1/trace_examples.json")

if __name__ == "__main__":
    main()
