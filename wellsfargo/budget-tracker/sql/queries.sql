-- fetch_categorized_transactions: retrieve all categorized transactions for a date range
SELECT
  t.row_hash,
  t.account_masked,
  t.txn_date,
  t.description_raw,
  t.amount,
  t.currency,
  COALESCE(c.category, 'uncategorized') AS category,
  COALESCE(c.category_source, 'none') AS category_source,
  c.confidence
FROM wf_transactions t
LEFT JOIN wf_txn_categories c ON c.row_hash = t.row_hash
WHERE t.txn_date >= %(start_date)s
  AND t.txn_date <= %(end_date)s
ORDER BY t.txn_date, t.row_hash;

-- fetch_budget_over_limit: categories exceeding their budget in the latest run
SELECT
  category,
  label,
  budget_amount,
  actual_amount,
  variance,
  utilization_pct
FROM wf_budget_categories c
JOIN wf_budget_runs r ON r.run_id = c.run_id
WHERE r.status = 'success'
  AND c.is_over_budget = TRUE
ORDER BY c.variance;
