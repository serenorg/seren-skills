-- fetch_categorized_transactions
SELECT t.row_hash, t.account_masked, t.txn_date, t.description_raw,
  t.amount, t.currency,
  COALESCE(c.category, 'uncategorized') AS category,
  COALESCE(c.category_source, 'none') AS category_source, c.confidence
FROM wf_transactions t
LEFT JOIN wf_txn_categories c ON c.row_hash = t.row_hash
WHERE t.txn_date >= %(start_date)s AND t.txn_date <= %(end_date)s
ORDER BY t.txn_date, t.row_hash;
