SELECT
    c.benchmark_name,
    c.variant,
    cc.condition_register,
    cc.condition_value,
    string_agg(sc.check_id, ', ' ORDER BY sc.syndrome_index) AS checks_that_fired,
    cc.gate,
    cc.target_qubit
FROM conditional_correction cc
JOIN circuit c ON c.circuit_id = cc.circuit_id
JOIN stabilizer_check sc
    ON sc.circuit_id = cc.circuit_id
    AND sc.syndrome_register = cc.condition_register
    AND (cc.condition_value >> sc.syndrome_index) % 2 = 1
GROUP BY c.benchmark_name, c.variant, cc.condition_register, cc.condition_value, cc.gate, cc.target_qubit
ORDER BY c.benchmark_name, c.variant, cc.condition_value;
