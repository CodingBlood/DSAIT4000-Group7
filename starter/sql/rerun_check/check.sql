SELECT 'experiment' AS table_name, COUNT(*) AS row_count, COUNT(DISTINCT experiment_id) AS distinct_ids, md5(string_agg(experiment_id, ',' ORDER BY experiment_id)) AS id_hash FROM experiment
UNION ALL
SELECT 'shot', COUNT(*), COUNT(DISTINCT shot_id), md5(string_agg(shot_id, ',' ORDER BY shot_id)) FROM shot
UNION ALL
SELECT 'shot_prediction', COUNT(*), COUNT(DISTINCT shot_id || decoder_id), md5(string_agg(shot_id || decoder_id, ',' ORDER BY shot_id, decoder_id)) FROM shot_prediction
UNION ALL
SELECT 'syndrome_observation', COUNT(*), COUNT(DISTINCT source_record_id), md5(string_agg(source_record_id, ',' ORDER BY source_record_id)) FROM syndrome_observation
UNION ALL
SELECT 'syndrome_pattern', COUNT(*), COUNT(DISTINCT pattern_id), md5(string_agg(pattern_id, ',' ORDER BY pattern_id)) FROM syndrome_pattern
UNION ALL
SELECT 'circuit', COUNT(*), COUNT(DISTINCT circuit_id), md5(string_agg(circuit_id, ',' ORDER BY circuit_id)) FROM circuit
UNION ALL
SELECT 'stabilizer_check', COUNT(*), COUNT(DISTINCT source_record_id), md5(string_agg(source_record_id, ',' ORDER BY source_record_id)) FROM stabilizer_check
UNION ALL
SELECT 'conditional_correction', COUNT(*), COUNT(DISTINCT source_record_id), md5(string_agg(source_record_id, ',' ORDER BY source_record_id)) FROM conditional_correction;
ORDER BY table_name;