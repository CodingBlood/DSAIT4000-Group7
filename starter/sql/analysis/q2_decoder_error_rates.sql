SELECT
    e.distance,
    e.center_row,
    e.center_col,
    sp.decoder_id,
    COUNT(*) AS shots,
    SUM(CASE WHEN sp.predicted_flip <> s.actual_observable_flip THEN 1 ELSE 0 END) AS decoder_errors,
    AVG(CASE WHEN sp.predicted_flip <> s.actual_observable_flip THEN 1.0 ELSE 0.0 END) AS logical_error_rate
FROM shot s
JOIN experiment e ON e.experiment_id = s.experiment_id
JOIN shot_prediction sp ON sp.shot_id = s.shot_id
GROUP BY e.distance, e.center_row, e.center_col, sp.decoder_id
ORDER BY e.distance, e.center_row, e.center_col, sp.decoder_id;
