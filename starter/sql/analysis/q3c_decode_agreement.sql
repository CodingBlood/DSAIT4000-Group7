-- Part I analysis question 3, second half.
--
-- The mapping query shows what the circuit does. This one checks whether it is
-- the *right* thing, using only the parity structure and no knowledge of what
-- the `if` statements say.
--
-- Minimum-weight decoding of a repetition code: a single bit-flip breaks
-- exactly the checks that contain the flipped qubit. Invert that and the
-- culprit is the data qubit which is
--
--     * inside every check that fired, and
--     * inside no check that stayed quiet.
--
-- Both halves are needed. When only check a[0] fires, q[0] and q[1] are both
-- inside every fired check, but q[1] also sits inside the quiet a[1], so q[0]
-- is the only candidate.
--
-- `decoding_implies` is derived from the circuit's cx/measure structure alone.
-- `circuit_repairs` is what the `if` statement actually does. They should be
-- equal, and `candidates` should be 1, meaning the syndrome identifies one
-- qubit unambiguously.

WITH correction_check AS (
    -- every check on the condition's register, flagged by whether this
    -- syndrome value fires it
    SELECT cc.source_record_id,
           cc.circuit_id,
           cc.condition_value,
           cc.target_qubit,
           sc.check_id,
           (((cc.condition_value >> sc.syndrome_index) & 1) = 1) AS fired
    FROM conditional_correction cc
    JOIN stabilizer_check sc
         ON sc.circuit_id        = cc.circuit_id
        AND sc.syndrome_register = cc.condition_register
),
fired_totals AS (
    SELECT source_record_id,
           count(*) FILTER (WHERE fired) AS fired_count
    FROM correction_check
    GROUP BY source_record_id
),
qubit_coverage AS (
    -- for each data qubit, how many fired and quiet checks contain it
    SELECT k.source_record_id,
           scd.data_qubit,
           count(*) FILTER (WHERE k.fired)     AS in_fired,
           count(*) FILTER (WHERE NOT k.fired) AS in_quiet
    FROM correction_check k
    JOIN stabilizer_check_data_qubit scd
         ON scd.circuit_id = k.circuit_id
        AND scd.check_id   = k.check_id
    GROUP BY k.source_record_id, scd.data_qubit
)
SELECT c.benchmark_name,
       c.variant,
       cc.condition_value,
       cc.target_qubit AS circuit_repairs,
       min(qc.data_qubit) FILTER (
           WHERE qc.in_fired = ft.fired_count AND qc.in_quiet = 0
       ) AS decoding_implies,
       count(*) FILTER (
           WHERE qc.in_fired = ft.fired_count AND qc.in_quiet = 0
       ) AS candidates
FROM conditional_correction cc
JOIN circuit c
     ON c.circuit_id = cc.circuit_id
JOIN fired_totals ft
     ON ft.source_record_id = cc.source_record_id
JOIN qubit_coverage qc
     ON qc.source_record_id = cc.source_record_id
GROUP BY c.benchmark_name,
         c.variant,
         cc.condition_value,
         cc.target_qubit
ORDER BY c.benchmark_name,
         c.variant,
         cc.condition_value;
