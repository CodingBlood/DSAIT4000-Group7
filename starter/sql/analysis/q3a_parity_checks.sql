SELECT
    c.benchmark_name,
    c.variant,
    sc.check_id,
    sc.ancilla_qubit,
    string_agg(dq.data_qubit, ', ' ORDER BY dq.data_qubit) AS data_qubits,
    sc.syndrome_register || '[' || sc.syndrome_index || ']' AS syndrome_bit
FROM circuit c
JOIN stabilizer_check sc ON sc.circuit_id = c.circuit_id
JOIN stabilizer_check_data_qubit dq ON dq.circuit_id = sc.circuit_id AND dq.check_id = sc.check_id
GROUP BY c.benchmark_name, c.variant, sc.check_id, sc.ancilla_qubit, sc.syndrome_register, sc.syndrome_index
ORDER BY c.benchmark_name, c.variant, sc.syndrome_index;
