-- Part I analysis question 3
-- "How does the repetition-code circuit map data qubits to parity-check
--  ancillas, syndrome bits, and conditional corrections?"
--
-- One row per (conditional correction, parity check that its syndrome value
-- fires). Reading left to right gives the whole chain the question asks for:
--
--     data qubits -> ancilla -> syndrome bit -> syndrome value -> recovery
--
-- Joins four Gold tables: conditional_correction, circuit, stabilizer_check,
-- stabilizer_check_data_qubit. This satisfies the brief's requirement that at
-- least one analysis query join three or more Gold tables.
--
-- The correction-to-check link is arithmetic, not a foreign key. OpenQASM
-- conditions compare a whole classical register against an integer, so
-- `if(syn==3)` means every bit set in 3 fired. Testing bit `syndrome_index` of
-- `condition_value` is what connects a recovery operation back to the checks
-- that triggered it, and it is only possible because stabilizer_check stores
-- the syndrome bit split into register and index rather than as the text
-- "syn[0]".

SELECT c.benchmark_name,
       c.variant,
       cc.condition_register || ' == ' || cc.condition_value   AS syndrome_value,
       sc.ancilla_qubit,
       sc.syndrome_register || '[' || sc.syndrome_index || ']' AS syndrome_bit,
       string_agg(DISTINCT scd.data_qubit, ', ')               AS checked_data_qubits,
       cc.gate || ' ' || cc.target_qubit                       AS recovery
FROM conditional_correction cc
JOIN circuit c
     ON c.circuit_id = cc.circuit_id
JOIN stabilizer_check sc
     ON sc.circuit_id        = cc.circuit_id
    AND sc.syndrome_register = cc.condition_register
    AND ((cc.condition_value >> sc.syndrome_index) & 1) = 1
JOIN stabilizer_check_data_qubit scd
     ON scd.circuit_id = sc.circuit_id
    AND scd.check_id   = sc.check_id
GROUP BY c.benchmark_name,
         c.variant,
         cc.condition_register,
         cc.condition_value,
         sc.ancilla_qubit,
         sc.syndrome_register,
         sc.syndrome_index,
         cc.gate,
         cc.target_qubit
ORDER BY c.benchmark_name,
         c.variant,
         cc.condition_value,
         sc.ancilla_qubit;
