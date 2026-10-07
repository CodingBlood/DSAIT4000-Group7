SELECT 'circuit_id = google experiment_id' AS candidate_join, COUNT(*) AS matching_rows
FROM circuit c
JOIN experiment e ON c.circuit_id = e.experiment_id
UNION ALL
SELECT 'circuit_id = syndrome experiment_id', COUNT(*)
FROM circuit c
JOIN syndrome_experiment se ON c.circuit_id = se.experiment_id
UNION ALL
SELECT 'qubit_count = google qubits (2 * distance * distance - 1)', COUNT(*)
FROM circuit c
JOIN experiment e ON c.qubit_count = 2 * e.distance * e.distance - 1
UNION ALL
SELECT 'measurement_count = google measurement_count', COUNT(*)
FROM circuit c
JOIN experiment e ON c.measurement_count = e.measurement_count
UNION ALL
SELECT 'stabilizer checks per circuit = google measure qubits (distance * distance - 1)', COUNT(*)
FROM (SELECT circuit_id, COUNT(*) AS checks FROM stabilizer_check GROUP BY circuit_id) k
JOIN experiment e ON k.checks = e.distance * e.distance - 1
UNION ALL
SELECT 'name contains d<distance>, joined to google shots', COUNT(*)
FROM circuit c
JOIN experiment e ON c.benchmark_name LIKE '%d' || e.distance || '%'
JOIN shot s ON s.experiment_id = e.experiment_id
UNION ALL
SELECT 'name contains d<distance>, joined to syndrome rows', COUNT(*)
FROM circuit c
JOIN syndrome_experiment se ON c.benchmark_name LIKE '%d' || se.distance || '%'
JOIN syndrome_observation o ON o.experiment_id = se.experiment_id
ORDER BY matching_rows, candidate_join;
