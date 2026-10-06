SELECT
    e.experiment_id,
    e.physical_fault_rate,
    COUNT(*) AS csv_rows,
    COUNT(DISTINCT o.pattern_id) AS distinct_syndromes,
    SUM(o.quantity) AS weighted_observations,
    SUM(CASE WHEN o.logical_error_label THEN o.quantity ELSE 0 END)::float / SUM(o.quantity) AS weighted_logical_error_rate,
    SUM(CASE WHEN p.syndrome_bits = decode(repeat('00', 16), 'hex') THEN o.quantity ELSE 0 END)::float / SUM(o.quantity) AS weighted_share_all_zero_syndrome,
    SUM(CASE WHEN p.syndrome_bits = decode(repeat('00', 16), 'hex') AND o.logical_error_label THEN o.quantity ELSE 0 END) AS logical_errors_with_all_zero_syndrome
FROM syndrome_observation o
JOIN syndrome_experiment e ON e.experiment_id = o.experiment_id
JOIN syndrome_pattern p ON p.pattern_id = o.pattern_id
GROUP BY e.experiment_id, e.physical_fault_rate
ORDER BY e.physical_fault_rate;
