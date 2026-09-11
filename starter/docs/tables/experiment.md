# experiment

**Path:** silver/google_qec/experiment.parquet
**Owner:** Aleksandr Petrov
**Status:** started

## What one row represents
see below

## Where each column comes from
| Column | Source | Notes |
|source_record_id| surface_code_bX_d3_r25_center_[]_[] | comes from the name of directory |
|experiment_id| surface_code_bX_d3_r25_center_[]_[] | in form of hex string|
|basis|surface_code_bX_d3_r25_center_[]_[] | derived from the name (X/Z)
|distance | surface_code_bX_d3_r25_center_[]_[] | derived from the name 
|rounds| surface_code_bX_d3_r25_center_[]_[] | derived from the name 
|shots|detection_events.b8| is the amount of chip runs performed per round
|center_row|surface_code_bX_d3_r25_center_[]_[] | derived from the name 
|center_col|surface_code_bX_d3_r25_center_[]_[] | derived from the name 
|measurement_count|measurements.b8| 
|detector_count|detection_events.b8|

## Our understanding of this table
the table tells for one experiment in a particular chip region (center row/col) how for a particular property measurement (basis) and error protection (code distance) experiment was performed for how many runs (shots) And within that shot how many times (rounds) the cycle was performed. it also records the amount of intermidiate measurements per shot (detector_count) and the final count of readings (measurement_count).

## Decisions made
-

## Open questions
-

## Validation checks implemented
- [ ]
