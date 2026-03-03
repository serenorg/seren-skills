CREATE OR REPLACE VIEW v_wf_latest_statements AS
SELECT sf.*
FROM wf_statement_files sf
JOIN wf_runs r ON r.run_id = sf.run_id
WHERE r.status = 'success'
AND r.ended_at = (
  SELECT MAX(r2.ended_at)
  FROM wf_runs r2
  WHERE r2.status = 'success'
);

CREATE OR REPLACE VIEW v_wf_transactions_clean AS
SELECT
  t.row_hash,
  t.run_id,
  t.file_id,
  t.account_masked,
  t.txn_date,
  t.post_date,
  t.description_raw,
  t.amount,
  t.currency,
  t.statement_period_start,
  t.statement_period_end,
  COALESCE(c.category, 'uncategorized') AS category,
  COALESCE(c.category_source, 'none') AS category_source,
  c.confidence,
  c.rationale_short
FROM wf_transactions t
LEFT JOIN wf_txn_categories c ON c.row_hash = t.row_hash;

CREATE OR REPLACE VIEW v_wf_monthly_cashflow AS
SELECT
  account_masked,
  month_start,
  SUM(debit_total) AS debit_total,
  SUM(credit_total) AS credit_total,
  SUM(txn_count) AS txn_count
FROM wf_monthly_summary
GROUP BY account_masked, month_start
ORDER BY month_start DESC, account_masked;
