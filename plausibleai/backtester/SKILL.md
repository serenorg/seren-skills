---
name: backtester
description: "Use to perform market backtests with PlausibleAI Backtester, including symbol discovery, strategy validation, strategy mining, and batch execution."
---
# PlausibleAI Backtester

## How to Use This Skill

When this skill is active, use the guidance below directly. Do not perform filesystem searches or tool-driven exploration to rediscover it.

Use the PlausibleAI publisher API as the source of truth for symbol discovery, DSL discovery, validation, and execution. Prefer validating unfamiliar payloads before running them.

## Base Route

All routes go through `https://api.serendb.com/publishers/plausibleai`.

## Authentication

All endpoints require `Authorization: Bearer $SEREN_API_KEY`.

## Workflow

1. Resolve auth.
Set `SEREN_API_KEY` for bearer auth. Use `SEREN_PUBLISHER_BASE_URL` in examples; default it to `https://api.serendb.com/publishers/plausibleai`.

2. Discover the market universe before guessing symbols.
Call `GET /api/markets/types` to see supported market types and symbol counts.
Call `GET /api/markets/symbols` with `market_type`, `search`, `limit`, and `offset` when the symbol is unknown.
Call `GET /api/markets/symbols/{symbol}` when the caller needs metadata or data availability.

3. Load the DSL contract.
Call `GET /api/backtests/catalog` before composing a new request shape.
Treat the catalog as authoritative for indicators, parameters, operators, logic nodes, examples, `price_adjustment_modes`, `entry_price_bases`, and response metric definitions.

4. Build the request with stable rule ids.
Every entry or exit rule must include a unique `id`.
Logic nodes reference rules by `id`, never by position.
If `logic` is omitted, the API combines all rules in the set with `AND`.

5. Validate novel requests.
Use `POST /api/backtests/validate` when the request uses a new symbol, a new indicator combination, or a non-trivial logic tree.
Surface validation errors directly instead of trying to guess what the API intended.

6. Execute.
Use `POST /api/backtests` for a single run.
Use `POST /api/backtests/batch` when the caller wants multiple independent runs. Batch requests run concurrently on the server; order in the response is stable regardless of completion order.
Single-run backtests are also stored as short-lived retrievable results. The response body is a compact stored-result summary with `id`, `expires_at`, and follow-up links for fetching the full result, trades, and equity curve.

7. Mine when the caller wants "the best actionable signal now".
Use `POST /api/backtests/mine`.
Minimal request is just `{ "symbol": "BTC-USD" }`.
Mining defaults to a sensible rolling window and ranks candidates by `profit_factor` unless overridden.
Mining returns a compact summary plus a nested `backtest` handle with `id`, `expires_at`, and follow-up links.

8. Retrieve large result sections incrementally.
Use `GET /api/backtests/{id}` for the stored full result.
Use `GET /api/backtests/{id}/trades` for the full trade list, or add `?limit=&offset=` when pagination is needed.
Use `GET /api/backtests/{id}/equity-curve` for the full equity curve, or add `?limit=&offset=` when pagination is needed.
Stored results are ephemeral and expire automatically.

9. Interpret the result carefully.
`report` is the summary.
`benchmarks.buy_and_hold` is the buy-and-hold comparison over the same range.
`execution.provider_symbol` shows the provider-native symbol actually used after the backend auto-resolves the best data source.
`trades` are closed trades. Each trade includes `trade_number`, `side`, `entry_bar_index`, `exit_bar_index`, `entry_date`, `exit_date`, `pnl`, `duration_bars`, and `exit_reason`.
`trades[].exit_reason` is a snake_case string from a documented set: `take_profit`, `stop_loss`, `trailing_stop`, `highest_high_exit`, `lowest_low_exit`, `exit_signal`, `end_of_data`, `other`. The full list is in `catalog.trade_exit_reasons`.
`equity_curve` is trade-indexed, not bar-indexed, and uses the same `trade_number` values as `trades`.
Top-level `first_entry_signal_at` and `last_entry_signal_at` refer to entry signals only.
`diagnostics` reports signal counts and per-rule signal summaries using rule ids.

## Indicator Quick Reference

| Key | Category | Required Params | Optional Params / Notes |
|-----|----------|-----------------|-------------------------|
| `sma` | trend | `period` (int) | `source` (default: `close`) |
| `ema` | trend | `period` (int) | `source` (default: `close`) |
| `adx` | trend | `period` (int) | — |
| `positive_directional_indicator` | trend | `period` (int) | — |
| `negative_directional_indicator` | trend | `period` (int) | — |
| `parabolic_sar` | trend | `af_step` (float, e.g. 0.02), `af_max` (float, e.g. 0.20) | Returns +1 (uptrend) or -1 (downtrend); compare against 0 |
| `rsi` | momentum | `period` (int) | `source` (default: `close`) |
| `stochastic_oscillator` | momentum | `period` (int) | range 0–100 |
| `momentum` | momentum | `period` (int) | — |
| `cci` | momentum | `period` (int) | — |
| `roc` | momentum | `period` (int) | — |
| `macd_line` | momentum | `fast`, `slow`, `signal` (all int, fast < slow) | — |
| `macd_signal_line` | momentum | `fast`, `slow`, `signal` (all int, fast < slow) | — |
| `macd_histogram` | momentum | `fast`, `slow`, `signal` (all int, fast < slow) | — |
| `tsi` | momentum | `long_period` (int), `short_period` (int, must be < long_period) | range: -100 to +100 |
| `atr` | volatility | `period` (int) | source not accepted |
| `atr_percent` | volatility | `period` (int) | — |
| `bollinger_upper_band` | volatility | `period`, `num_std` | — |
| `bollinger_lower_band` | volatility | `period`, `num_std` | — |
| `standard_deviation` | volatility | `period` (int) | `source` (default: `close`) |
| `keltner_upper_band` | volatility | `period` (int), `multiplier` (float) | — |
| `keltner_lower_band` | volatility | `period` (int), `multiplier` (float) | — |
| `highest` | price_action | `period` (int) | `source` (default: `high`) |
| `lowest` | price_action | `period` (int) | `source` (default: `low`) |
| `day_of_week` | seasonal | — | Sun=0, Mon=1 … Fri=5, Sat=6; use `eq` to target a specific day |
| `day_of_month` | seasonal | — | 1–31 |
| `week_of_month` | seasonal | — | 1–5; resets on month change |
| `month` | seasonal | — | 1–12 |
| `quarter` | seasonal | — | 1–4 |

`period` is always required — the server never defaults it. The catalog `indicators[].parameters` array is the source of truth.

## Execution Block Quick Reference

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `side` | `"long"` \| `"short"` | yes | trade direction |
| `entry_mode` | enum | yes | `this_bar_close`, `next_bar_open` (most common), `next_bar_limit`, `next_bar_stop` |
| `atr_period` | integer | no | used when any ATR-based entry offset or exit is present; defaults to `20` when omitted |
| `entry_price` | `{basis, lookback, offset}` | no | only valid with `next_bar_limit` or `next_bar_stop`; bases: `none`, `highest_high`, `lowest_low`; `offset` is `{mode, value}` and moves the reference price up or down |

## Exit Policy Quick Reference

Price-based exits go in `exits`. A rule-based signal exit goes in `exit_signal`.

| Field | Type | Mode options | Notes |
|-------|------|--------------|-------|
| `exits.stop_loss` | `{mode, value}` | `fixed`, `percent`, `atr` | value > 0 |
| `exits.take_profit` | `{mode, value}` | `fixed`, `percent`, `atr` | value > 0 |
| `exits.trailing_stop` | `{mode, value}` | `fixed`, `percent`, `atr` | value > 0 |
| `exits.max_hold_bars` | integer | — | exits after N bars |
| `exits.profitable_closes` | integer | — | exits after N cumulative profitable closes since entry |
| `exits.highest_high_exit_lookback` | integer | — | exits at the rolling highest high over N bars |
| `exits.lowest_low_exit_lookback` | integer | — | exits at the rolling lowest low over N bars |
| `exit_signal` | rule set | — | rule-based exit logic that can be combined with price exits |

If any ATR-based entry offset or exit is present and `execution.atr_period` is omitted, the API defaults it to `20`. `entry_price` is only valid with `entry_mode: next_bar_limit` or `next_bar_stop`.

## Example Strategies

Use these as canonical request patterns for the main DSL surfaces.

### 1. Trend Following: 50/200 SMA Golden Cross

Good default example for rule ids and `exit_signal`.

```json
{
  "symbol": "BTC-USD",
  "timeframe": "daily",
  "start_at": "2020-01-01",
  "initial_capital": 100000,
  "execution": {
    "side": "long",
    "entry_mode": "next_bar_open"
  },
  "entry": {
    "rules": [
      {
        "id": "golden_cross",
        "lhs": {
          "indicator": {
            "key": "sma",
            "params": {
              "period": 50,
              "source": "close"
            }
          }
        },
        "operator": "crosses_above",
        "rhs": {
          "indicator": {
            "key": "sma",
            "params": {
              "period": 200,
              "source": "close"
            }
          }
        }
      }
    ],
    "logic": {
      "type": "rule",
      "id": "golden_cross"
    }
  },
  "exit_signal": {
    "rules": [
      {
        "id": "death_cross",
        "lhs": {
          "indicator": {
            "key": "sma",
            "params": {
              "period": 50,
              "source": "close"
            }
          }
        },
        "operator": "crosses_below",
        "rhs": {
          "indicator": {
            "key": "sma",
            "params": {
              "period": 200,
              "source": "close"
            }
          }
        }
      }
    ],
    "logic": {
      "type": "rule",
      "id": "death_cross"
    }
  }
}
```

### 2. Mean Reversion: RSI Oversold Bounce

Good example for scalar thresholds plus `stop_loss` and `take_profit`.

```json
{
  "symbol": "AAPL",
  "timeframe": "daily",
  "start_at": "2020-01-01",
  "initial_capital": 100000,
  "execution": {
    "side": "long",
    "entry_mode": "next_bar_open"
  },
  "entry": {
    "rules": [
      {
        "id": "rsi_oversold",
        "lhs": {
          "indicator": {
            "key": "rsi",
            "params": {
              "period": 14,
              "source": "close"
            }
          }
        },
        "operator": "lte",
        "rhs": {
          "value": 30
        }
      }
    ],
    "logic": {
      "type": "rule",
      "id": "rsi_oversold"
    }
  },
  "exits": {
    "stop_loss": {
      "mode": "percent",
      "value": 5
    },
    "take_profit": {
      "mode": "percent",
      "value": 10
    },
    "max_hold_bars": 20
  }
}
```

### 3. Trend Breakout: Stop Above 55-Bar High

Good example for a more canonical Donchian-style trend-following breakout with a long-term trend filter.

```json
{
  "symbol": "BTC-USD",
  "timeframe": "daily",
  "start_at": "2020-01-01",
  "initial_capital": 100000,
  "execution": {
    "side": "long",
    "entry_mode": "next_bar_stop",
    "entry_price": {
      "basis": "highest_high",
      "lookback": 55,
      "offset": {
        "mode": "fixed",
        "value": 0
      }
    }
  },
  "entry": {
    "rules": [
      {
        "id": "above_sma_200",
        "lhs": {
          "field": "close"
        },
        "operator": "gte",
        "rhs": {
          "indicator": {
            "key": "sma",
            "params": {
              "period": 200,
              "source": "close"
            }
          }
        }
      }
    ],
    "logic": {
      "type": "rule",
      "id": "above_sma_200"
    }
  },
  "exits": {
    "lowest_low_exit_lookback": 20
  }
}
```

## Curl Reference

Use these snippets directly when you need to query or execute against the API.

### Base Variables

```bash
SEREN_PUBLISHER_BASE_URL="${SEREN_PUBLISHER_BASE_URL:-https://api.serendb.com/publishers/plausibleai}"
SEREN_API_KEY="${SEREN_API_KEY:?Set SEREN_API_KEY}"
```

Every request uses:

```bash
-H "Authorization: Bearer $SEREN_API_KEY"
```

### Market Discovery

List market types:

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/markets/types" \
  -H "Authorization: Bearer $SEREN_API_KEY" | jq
```

Search symbols:

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/markets/symbols?market_type=crypto&search=bitcoin&limit=20" \
  -H "Authorization: Bearer $SEREN_API_KEY" | jq
```

Get symbol detail:

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/markets/symbols/BTC-USD" \
  -H "Authorization: Bearer $SEREN_API_KEY" | jq
```

### DSL Discovery

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/backtests/catalog" \
  -H "Authorization: Bearer $SEREN_API_KEY" | jq
```

### Validate a Backtest

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/backtests/validate" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "symbol": "BTC-USD",
    "timeframe": "daily",
    "start_at": "2020-01-01",
    "initial_capital": 100000,
    "execution": {
      "side": "long",
      "entry_mode": "next_bar_open"
    },
    "entry": {
      "rules": [
        {
          "id": "golden_cross",
          "lhs": {
            "indicator": {
              "key": "sma",
              "params": {
                "period": 50,
                "source": "close"
              }
            }
          },
          "operator": "crosses_above",
          "rhs": {
            "indicator": {
              "key": "sma",
              "params": {
                "period": 200,
                "source": "close"
              }
            }
          }
        }
      ],
      "logic": {
        "type": "rule",
        "id": "golden_cross"
      }
    },
    "exit_signal": {
      "rules": [
        {
          "id": "death_cross",
          "lhs": {
            "indicator": {
              "key": "sma",
              "params": {
                "period": 50,
                "source": "close"
              }
            }
          },
          "operator": "crosses_below",
          "rhs": {
            "indicator": {
              "key": "sma",
              "params": {
                "period": 200,
                "source": "close"
              }
            }
          }
        }
      ],
      "logic": {
        "type": "rule",
        "id": "death_cross"
      }
    }
  }' | jq
```

### Execute a Backtest

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/backtests" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "symbol": "BTC-USD",
    "timeframe": "daily",
    "start_at": "2020-01-01",
    "initial_capital": 100000,
    "execution": {
      "side": "long",
      "entry_mode": "next_bar_open"
    },
    "entry": {
      "rules": [
        {
          "id": "golden_cross",
          "lhs": {
            "indicator": {
              "key": "sma",
              "params": {
                "period": 50,
                "source": "close"
              }
            }
          },
          "operator": "crosses_above",
          "rhs": {
            "indicator": {
              "key": "sma",
              "params": {
                "period": 200,
                "source": "close"
              }
            }
          }
        }
      ],
      "logic": {
        "type": "rule",
        "id": "golden_cross"
      }
    },
    "exit_signal": {
      "rules": [
        {
          "id": "death_cross",
          "lhs": {
            "indicator": {
              "key": "sma",
              "params": {
                "period": 50,
                "source": "close"
              }
            }
          },
          "operator": "crosses_below",
          "rhs": {
            "indicator": {
              "key": "sma",
              "params": {
                "period": 200,
                "source": "close"
              }
            }
          }
        }
      ],
      "logic": {
        "type": "rule",
        "id": "death_cross"
      }
    }
  }' | jq
```

The response from `POST /api/backtests` is a compact stored-result summary. Use the returned `id` or `links.full_result_path` to fetch the full backtest result when needed.

### Retrieve a Stored Backtest Result

Use the `id` returned in the compact response from `POST /api/backtests` or `POST /api/backtests/mine`.

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/backtests/<BACKTEST_ID>" \
  -H "Authorization: Bearer $SEREN_API_KEY" | jq
```

Retrieve all trades:

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/backtests/<BACKTEST_ID>/trades" \
  -H "Authorization: Bearer $SEREN_API_KEY" | jq
```

Retrieve paginated trades when needed:

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/backtests/<BACKTEST_ID>/trades?limit=100&offset=0" \
  -H "Authorization: Bearer $SEREN_API_KEY" | jq
```

Retrieve the full equity curve:

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/backtests/<BACKTEST_ID>/equity-curve" \
  -H "Authorization: Bearer $SEREN_API_KEY" | jq
```

Retrieve paginated equity curve when needed:

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/backtests/<BACKTEST_ID>/equity-curve?limit=100&offset=0" \
  -H "Authorization: Bearer $SEREN_API_KEY" | jq
```

### Mine an Actionable Strategy

Minimal mining request:

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/backtests/mine" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "symbol": "BTC-USD"
  }' | jq
```

Mining returns:

- `symbol`
- `signal_at`
- `fitness_metric`
- `fitness_value`
- `mined_candidates`
- `actionable_candidates`
- `rank`
- `backtest`

`backtest` contains the stored result handle and compact summary:

- `id`
- `kind`
- `created_at`
- `expires_at`
- `request`
- `summary`
- `links.full_result_path`
- `links.trades_path`
- `links.equity_curve_path`

### Batch Execution

```bash
curl -sS "$SEREN_PUBLISHER_BASE_URL/api/backtests/batch" \
  -H "Authorization: Bearer $SEREN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "requests": [
      {
        "symbol": "BTC-USD",
        "timeframe": "daily",
        "start_at": "2020-01-01",
        "initial_capital": 100000,
        "execution": {
          "side": "long",
          "entry_mode": "next_bar_open"
        },
        "entry": {
          "rules": [
            {
              "id": "golden_cross",
              "lhs": {
                "indicator": {
                  "key": "sma",
                  "params": {
                    "period": 50,
                    "source": "close"
                  }
                }
              },
              "operator": "crosses_above",
              "rhs": {
                "indicator": {
                  "key": "sma",
                  "params": {
                    "period": 200,
                    "source": "close"
                  }
                }
              }
            }
          ],
          "logic": {
            "type": "rule",
            "id": "golden_cross"
          }
        },
        "exit_signal": {
          "rules": [
            {
              "id": "death_cross",
              "lhs": {
                "indicator": {
                  "key": "sma",
                  "params": {
                    "period": 50,
                    "source": "close"
                  }
                }
              },
              "operator": "crosses_below",
              "rhs": {
                "indicator": {
                  "key": "sma",
                  "params": {
                    "period": 200,
                    "source": "close"
                  }
                }
              }
            }
          ],
          "logic": {
            "type": "rule",
            "id": "death_cross"
          }
        }
      }
    ]
  }' | jq
```

## DSL Rules

- Keep rule ids short and stable. Use letters, numbers, underscores, or hyphens only.
- Use `bars_ago` on `lhs` or `rhs` when you need to compare against an earlier bar. Omit it for the current bar.
- Use `crosses_above` and `crosses_below` only when prior-bar behavior is intended.
- Prefer `gte` or `lte` over `eq` or `ne` for floating-point comparisons.
- Remember that `xor` uses parity semantics: it is true when an odd number of child nodes are true.
- `catalog.limits.max_bars_ago` is the maximum allowed `bars_ago` value.
- The `atr` indicator only accepts `period`. Passing `source` to `atr` is a validation error.
- Indicator `period` params are always required. The catalog lists no default value for them; omitting `period` returns a 422.
- Add `"negate": true` to any rule to invert its signal (fires when the condition is NOT met). Cannot be combined with `crosses_above` or `crosses_below` — use the complementary operator instead. For compound negation, apply `negate` to individual rules and combine with `any`/`all` logic nodes using De Morgan's laws.

## Common Pitfalls

| Mistake | Fix |
|---------|-----|
| Omitting `id` on a rule | Every rule in `rules[]` must have a unique `id`; logic nodes reference it |
| Guessing indicator defaults | Always provide `period`; there is no server default |
| Passing `source` to `atr` | ATR does not accept source; use `period` only |
| Using `eq`/`ne` on float indicators | Use `gte`/`lte` range checks instead |
| `crosses_above` with too little data | Cross operators need one additional prior bar beyond the indicator lookback |
| `"negate": true` with `crosses_above`/`crosses_below` | Not allowed — cross operators fire on a single bar; their negation fires on ~99% of bars. Use the complementary operator instead |
| `af_step > af_max` on `parabolic_sar` | Validation error — step must be ≤ max |
| `short_period >= long_period` on `tsi` | Validation error — short must be < long |
| `exit_signal` and `exits` both provided | Both are valid simultaneously; `exits` handles price levels, `exit_signal` handles rule-based signals |
| `entry_price` with `next_bar_open` | `entry_price` only works with `next_bar_limit` or `next_bar_stop` |
| Logic node referencing undefined id | Logic node `id` must exactly match a rule `id` in the same `rules[]` array |
| No signals firing | Check `diagnostics.entry.rules[]` per-rule signal counts; adjust lookback, threshold, or date range |

## Response Discipline

- Prefer returning concise summaries unless the user asks for raw JSON.
- `POST /api/backtests` and `POST /api/backtests/mine` both return compact stored-result summaries first; do not assume the full backtest payload is in the initial response.
- For stored results, summarize the compact summary first and only fetch the full result, `trades`, or `equity_curve` when the user needs that detail.
- Use `?limit=&offset=` for `trades` or `equity_curve` only when the result is large enough that incremental retrieval is useful.
- When showing example payloads, include rule ids explicitly.
- When the symbol is uncertain, use the market endpoints first instead of inventing tickers.
- When a backtest output looks surprising, compare `trades`, `equity_curve`, and `diagnostics` before assuming the engine is wrong.
