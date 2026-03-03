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

-- fetch_active_recurring: retrieve all currently active recurring patterns
SELECT
  payee_normalized,
  category,
  frequency,
  avg_amount,
  occurrence_count,
  confidence,
  last_seen,
  next_expected
FROM wf_recurring_patterns p
JOIN wf_recurring_runs r ON r.run_id = p.run_id
WHERE r.status = 'success'
  AND p.is_active = TRUE
ORDER BY p.avg_amount DESC;
