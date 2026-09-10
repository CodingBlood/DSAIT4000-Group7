# Submission Checklist

## Part I
- [ ] All 6 Silver tables produced, matching silver-tables.md exactly
- [ ] data_issues.parquet has required columns and real entries
- [ ] source_trace.parquet covers every Silver row
- [ ] row_counts.json: read = accepted + rejected, for every table
- [ ] Gold schema created, with keys/constraints, one-sentence-per-table doc written
- [ ] Rejected join documented with evidence
- [ ] `make run` works clean on a fresh reset
- [ ] Rerun produces identical records/IDs (idempotency check)

## Part II
- [ ] Task A, B, C implemented
- [ ] predictions.parquet + metrics.json produced
- [ ] report.md written

## Before submitting
- [ ] `make test` passes
- [ ] No secrets/.env committed
- [ ] README updated if setup steps changed
- [ ] Everyone's branch merged into main