"""Local-pull runner for seren-cron tick claims.

Long-lived process that polls seren-cron and dispatches due ticks to
`scripts/agent.py`. Implemented in phase 5.
"""
