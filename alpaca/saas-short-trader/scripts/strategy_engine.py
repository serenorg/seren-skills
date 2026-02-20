#!/usr/bin/env python3
"""
Core execution engine for saas-short-trader.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from self_learning import ensure_champion, run_label_update
from seren_client import SerenClient
from serendb_storage import SerenDBStorage


DEFAULT_UNIVERSE = [
    "ADBE",
    "CRM",
    "NOW",
    "HUBS",
    "TEAM",
    "ZS",
    "CRWD",
    "OKTA",
    "DDOG",
    "MDB",
    "SNOW",
    "GTLB",
    "ESTC",
    "SMAR",
    "DOCU",
    "U",
    "PATH",
    "BILL",
    "INTA",
    "CFLT",
    "NET",
    "SHOP",
    "TWLO",
    "RBLX",
    "ASAN",
    "BOX",
    "APPF",
    "AVDX",
    "PAYC",
    "WK",
]

TICKER_COMPANY_MAP = {
    "ADBE": "Adobe",
    "CRM": "Salesforce",
    "NOW": "ServiceNow",
    "HUBS": "HubSpot",
    "TEAM": "Atlassian",
    "ZS": "Zscaler",
    "CRWD": "CrowdStrike",
    "OKTA": "Okta",
    "DDOG": "Datadog",
    "MDB": "MongoDB",
    "SNOW": "Snowflake",
    "GTLB": "GitLab",
    "ESTC": "Elastic",
    "SMAR": "Smartsheet",
    "DOCU": "DocuSign",
    "U": "Unity",
    "PATH": "UiPath",
    "BILL": "Bill.com",
    "INTA": "Intapp",
    "CFLT": "Confluent",
    "NET": "Cloudflare",
    "SHOP": "Shopify",
    "TWLO": "Twilio",
    "RBLX": "Roblox",
    "ASAN": "Asana",
    "BOX": "Box",
    "APPF": "AppFolio",
    "AVDX": "AvidXchange",
    "PAYC": "Paycom",
    "WK": "Workiva",
}

WEIGHTS = {"f": 0.30, "a": 0.30, "s": 0.20, "t": 0.20, "p": 1.00}


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class FeedResult:
    ok: bool
    data: Dict[str, Dict[str, Any]]
    error: str = ""


class StrategyEngine:
    def __init__(
        self,
        dsn: str,
        api_key: Optional[str] = None,
        strict_required_feeds: bool = True,
    ):
        self.storage = SerenDBStorage(dsn)
        self.strict_required_feeds = strict_required_feeds
        self.api_key = api_key or os.getenv("SEREN_API_KEY")
        self.seren: Optional[SerenClient] = None
        if self.api_key:
            self.seren = SerenClient(api_key=self.api_key)

    def ensure_schema(self) -> None:
        root = Path(__file__).resolve().parent
        self.storage.ensure_schemas(
            base_sql=root / "serendb_schema.sql",
            learning_sql=root / "self_learning_schema.sql",
        )

    def run_scan(
        self,
        mode: str = "paper-sim",
        run_profile: str = "continuous",
        run_type: str = "scan",
        universe: Optional[List[str]] = None,
        max_names_scored: int = 30,
        max_names_orders: int = 8,
        min_conviction: float = 65.0,
        learning_mode: str = "adaptive-paper",
        scheduled_window_start: Optional[str] = None,
    ) -> Dict[str, Any]:
        universe = (universe or DEFAULT_UNIVERSE)[:max_names_scored]
        overlap_id = self.storage.check_overlap(mode=mode, run_type=run_type)
        if overlap_id:
            return {
                "status": "blocked_overlap",
                "mode": mode,
                "run_type": run_type,
                "blocking_run_id": overlap_id,
            }

        metadata = {
            "run_type": run_type,
            "run_profile": run_profile,
            "learning_mode": learning_mode,
            "scheduled_window_start": scheduled_window_start or datetime.now(timezone.utc).isoformat(),
            "idempotency_key": f"saas-short-trader:{mode}:{run_type}:{scheduled_window_start or date.today()}",
        }
        run_id = self.storage.insert_run(
            mode=mode,
            universe=universe,
            max_names_scored=max_names_scored,
            max_names_orders=max_names_orders,
            min_conviction=min_conviction,
            status="running",
            metadata=metadata,
        )

        try:
            sec_result = self.fetch_sec_features(universe)
            trends_result = self.fetch_trends_features(universe)
            news_result = self.fetch_news_features(universe)
            market_result = self.fetch_market_features(universe)

            feed_status = {
                "sec-filings-intelligence": sec_result.ok,
                "google-trends": trends_result.ok,
                "news-search": news_result.ok,
                "alpaca": market_result.ok,
            }
            feed_errors = {
                "sec-filings-intelligence": sec_result.error,
                "google-trends": trends_result.error,
                "news-search": news_result.error,
                "alpaca": market_result.error,
            }

            if self.strict_required_feeds and (not sec_result.ok or not trends_result.ok or not news_result.ok):
                self.storage.update_run_status(
                    run_id,
                    "blocked",
                    {
                        "feed_status": feed_status,
                        "feed_errors": feed_errors,
                        "blocked_reason": "required_feed_failure",
                    },
                )
                return {
                    "status": "blocked",
                    "run_id": run_id,
                    "mode": mode,
                    "run_type": run_type,
                    "feed_status": feed_status,
                    "feed_errors": feed_errors,
                }

            scored_rows = self.score_universe(
                universe=universe,
                sec_data=sec_result.data,
                trends_data=trends_result.data,
                news_data=news_result.data,
                market_data=market_result.data,
                min_conviction=min_conviction,
                max_names_orders=max_names_orders,
            )
            self.storage.insert_candidate_scores(run_id, scored_rows)

            selected = [r for r in scored_rows if r["selected"]]
            orders = self.build_orders(selected, portfolio_notional_usd=100000.0)
            self.storage.insert_order_events(run_id, mode, orders)

            sim = self.simulate(selected, orders)
            marks = self.build_marks_from_orders(orders, sim["mark_map"], run_id)
            self.storage.upsert_position_marks(date.today(), mode, marks, source_run_id=run_id)

            self.storage.upsert_pnl_daily(
                as_of_date=date.today(),
                mode=mode,
                realized_pnl=0.0,
                unrealized_pnl=sim["net_pnl_5d"],
                gross_exposure=sim["gross_exposure"],
                net_exposure=-sim["gross_exposure"],
                hit_rate=sim["hit_rate_5d"],
                max_drawdown=sim["max_drawdown"],
                source_run_id=run_id,
            )
            # Keep reporting rows aligned across paper/paper-sim/live.
            if mode == "paper-sim":
                self.storage.upsert_pnl_daily(
                    as_of_date=date.today(),
                    mode="paper",
                    realized_pnl=0.0,
                    unrealized_pnl=sim["net_pnl_5d"],
                    gross_exposure=sim["gross_exposure"],
                    net_exposure=-sim["gross_exposure"],
                    hit_rate=sim["hit_rate_5d"],
                    max_drawdown=sim["max_drawdown"],
                    source_run_id=run_id,
                )
            self.storage.upsert_pnl_daily(
                as_of_date=date.today(),
                mode="live",
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                gross_exposure=0.0,
                net_exposure=0.0,
                hit_rate=0.0,
                max_drawdown=sim["max_drawdown"],
                source_run_id=run_id,
            )

            metadata_patch = {
                "feed_status": feed_status,
                "feed_errors": feed_errors,
                "sim_windows": {
                    "5D_net_pnl": round(sim["net_pnl_5d"], 2),
                    "10D_net_pnl": round(sim["net_pnl_10d"], 2),
                    "20D_net_pnl": round(sim["net_pnl_20d"], 2),
                    "hit_rate_5D": round(sim["hit_rate_5d"], 4),
                    "hit_rate_10D": round(sim["hit_rate_10d"], 4),
                    "hit_rate_20D": round(sim["hit_rate_20d"], 4),
                },
                "selected_count": len(selected),
                "data_sources": ["alpaca", "sec-filings-intelligence", "google-trends", news_result.data.get("_source", "exa")],
            }
            self.storage.update_run_status(run_id, "completed", metadata_patch)
            return {
                "status": "completed",
                "run_id": run_id,
                "mode": mode,
                "run_type": run_type,
                "selected": [r["ticker"] for r in selected],
                "sim": sim,
                "feed_status": feed_status,
            }
        except Exception as exc:
            self.storage.update_run_status(run_id, "failed", {"error": str(exc)})
            raise

    def run_monitor(
        self,
        mode: str = "paper-sim",
        run_profile: str = "continuous",
        run_type: str = "monitor",
    ) -> Dict[str, Any]:
        overlap_id = self.storage.check_overlap(mode=mode, run_type=run_type)
        if overlap_id:
            return {"status": "blocked_overlap", "run_type": run_type, "blocking_run_id": overlap_id}

        run_id = self.storage.insert_run(
            mode=mode,
            universe=[],
            max_names_scored=30,
            max_names_orders=8,
            min_conviction=65.0,
            status="running",
            metadata={"run_type": run_type, "run_profile": run_profile},
        )
        try:
            latest_orders = self.storage.get_latest_selected_orders(mode=mode)
            if not latest_orders:
                self.storage.update_run_status(run_id, "blocked", {"blocked_reason": "no_open_strategy_orders"})
                return {"status": "blocked", "run_id": run_id, "reason": "no_open_strategy_orders"}

            tickers = [o["ticker"] for o in latest_orders]
            market = self.fetch_market_features(tickers)

            marks = []
            wins = 0
            gross = 0.0
            total_unrealized = 0.0
            for order in latest_orders:
                details = order.get("details") or {}
                entry = safe_float(details.get("entry_price"))
                qty = safe_float(order.get("qty"))
                ticker = order["ticker"]
                mark = safe_float((market.data.get(ticker) or {}).get("price"), entry)
                if mark <= 0:
                    mark = entry
                unrealized = (entry - mark) * qty
                wins += 1 if unrealized > 0 else 0
                gross += abs(entry * qty)
                total_unrealized += unrealized
                marks.append(
                    {
                        "ticker": ticker,
                        "qty": qty,
                        "avg_entry_price": entry,
                        "mark_price": mark,
                        "market_value": mark * qty,
                        "realized_pnl": 0.0,
                        "unrealized_pnl": unrealized,
                        "gross_exposure": abs(entry * qty),
                        "net_exposure": -abs(entry * qty),
                    }
                )

            self.storage.upsert_position_marks(date.today(), mode, marks, source_run_id=run_id)
            hit_rate = wins / max(1, len(marks))
            max_drawdown = self.compute_drawdown(mode, total_unrealized)
            self.storage.upsert_pnl_daily(
                as_of_date=date.today(),
                mode=mode,
                realized_pnl=0.0,
                unrealized_pnl=total_unrealized,
                gross_exposure=gross,
                net_exposure=-gross,
                hit_rate=hit_rate,
                max_drawdown=max_drawdown,
                source_run_id=run_id,
            )
            self.storage.update_run_status(run_id, "completed", {"symbols": tickers, "market_feed_ok": market.ok})
            return {
                "status": "completed",
                "run_id": run_id,
                "mode": mode,
                "run_type": run_type,
                "symbols": tickers,
                "unrealized_pnl": round(total_unrealized, 6),
                "hit_rate": round(hit_rate, 4),
            }
        except Exception as exc:
            self.storage.update_run_status(run_id, "failed", {"error": str(exc)})
            raise

    def run_post_close(self, mode: str = "paper-sim", run_profile: str = "continuous") -> Dict[str, Any]:
        run_type = "post-close"
        overlap_id = self.storage.check_overlap(mode=mode, run_type=run_type)
        if overlap_id:
            return {"status": "blocked_overlap", "run_type": run_type, "blocking_run_id": overlap_id}

        run_id = self.storage.insert_run(
            mode=mode,
            universe=[],
            max_names_scored=30,
            max_names_orders=8,
            min_conviction=65.0,
            status="running",
            metadata={"run_type": run_type, "run_profile": run_profile},
        )
        try:
            monitor_result = self.run_monitor(mode=mode, run_profile=run_profile, run_type="post-close-monitor")
            with self.storage.connect() as conn:
                ensure_champion(conn)
                label_result = run_label_update(conn, mode=mode)
            self.storage.update_run_status(
                run_id,
                "completed",
                {"monitor_result": monitor_result, "label_update": label_result},
            )
            return {"status": "completed", "run_id": run_id, "monitor": monitor_result, "label_update": label_result}
        except Exception as exc:
            self.storage.update_run_status(run_id, "failed", {"error": str(exc)})
            raise

    def compute_drawdown(self, mode: str, current_net: float) -> float:
        series = self.storage.get_pnl_series(mode=mode) + [current_net]
        if not series:
            return 0.0
        peak = -10**18
        max_dd = 0.0
        for v in series:
            peak = max(peak, v)
            max_dd = max(max_dd, peak - v)
        return max_dd

    def fetch_sec_features(self, tickers: List[str]) -> FeedResult:
        if not self.seren:
            return FeedResult(ok=False, data={}, error="SEREN_API_KEY missing")

        values_parts: List[str] = []
        for ticker in tickers:
            company = TICKER_COMPANY_MAP.get(ticker, ticker).replace("'", "''")
            values_parts.append(f"('{ticker}', '{company}')")
        values = ", ".join(values_parts)
        query = f"""
        WITH input(ticker, company_pattern) AS (
          VALUES {values}
        )
        SELECT
          i.ticker,
          MAX(f.filing_date)::date AS latest_filing_date,
          (ARRAY_AGG(f.filing_type ORDER BY f.filing_date DESC))[1] AS latest_filing_type,
          COUNT(f.*) AS filing_count,
          SUM(CASE WHEN LOWER(COALESCE(f.content, '')) LIKE '%guidance%' THEN 1 ELSE 0 END) AS guidance_mentions,
          SUM(CASE WHEN LOWER(COALESCE(f.content, '')) LIKE '%competition%' THEN 1 ELSE 0 END) AS competition_mentions,
          SUM(CASE WHEN LOWER(COALESCE(f.content, '')) LIKE '%ai%' OR LOWER(COALESCE(f.content, '')) LIKE '%artificial intelligence%' THEN 1 ELSE 0 END) AS ai_mentions,
          SUM(CASE WHEN LOWER(COALESCE(f.content, '')) LIKE '%churn%' THEN 1 ELSE 0 END) AS churn_mentions
        FROM input i
        LEFT JOIN public.filing f
          ON LOWER(f.company_name) LIKE '%' || LOWER(i.company_pattern) || '%'
        GROUP BY i.ticker
        ORDER BY i.ticker;
        """
        try:
            resp = self.seren.call_publisher("sec-filings-intelligence", method="POST", path="/", query=query, timeout=90)
            rows = self.seren.extract_rows(resp)
            data = {r["ticker"]: r for r in rows if r.get("ticker")}
            return FeedResult(ok=len(data) > 0, data=data, error="" if data else "no_rows")
        except Exception as exc:
            return FeedResult(ok=False, data={}, error=str(exc))

    def fetch_trends_features(self, tickers: List[str]) -> FeedResult:
        if not self.seren:
            return FeedResult(ok=False, data={}, error="SEREN_API_KEY missing")

        result: Dict[str, Dict[str, Any]] = {}
        errors: List[str] = []
        chunk_size = 4
        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i : i + chunk_size]
            keywords = chunk + ["chatgpt"]
            body_variants = [
                {"keywords": keywords, "timeframe": "today 3-m"},
                {"terms": keywords, "time_range": "today 3-m"},
                {"q": keywords, "window": "90d"},
            ]
            paths = ["/interest", "/trends", "/api/trends", "/"]
            ok = False
            for path in paths:
                if ok:
                    break
                for body in body_variants:
                    try:
                        resp = self.seren.call_publisher("google-trends", method="POST", path=path, body=body, timeout=45)
                        parsed = self.parse_trends_response(resp, chunk)
                        if parsed:
                            result.update(parsed)
                            ok = True
                            break
                    except Exception as exc:
                        errors.append(str(exc))
            if not ok:
                for t in chunk:
                    result[t] = {"avg_interest": 0, "source": "google-trends-fallback"}
        success = any((result.get(t, {}).get("source", "").startswith("google-trends")) for t in tickers)
        return FeedResult(ok=success, data=result, error="; ".join(errors[-3:]) if errors else "")

    def parse_trends_response(self, resp: Dict[str, Any], tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        body = self.seren.unwrap_body(resp)
        out: Dict[str, Dict[str, Any]] = {}

        def add(ticker: str, value: float) -> None:
            out[ticker] = {"avg_interest": int(round(clamp(value, 0, 100))), "source": "google-trends"}

        if isinstance(body, dict):
            rows = []
            if isinstance(body.get("data"), list):
                rows = body["data"]
            elif isinstance(body.get("rows"), list):
                rows = body["rows"]
            elif isinstance(body.get("result"), list):
                rows = body["result"]
            for r in rows:
                k = str(r.get("keyword") or r.get("term") or r.get("ticker") or "").upper()
                if k in tickers:
                    add(k, safe_float(r.get("avg_interest") or r.get("value") or r.get("score"), 0))

            # map shape: {"AAPL":[...], "MSFT":[...]}
            for t in tickers:
                if t in out:
                    continue
                series = body.get(t) or body.get(t.lower()) or body.get(t.upper())
                if isinstance(series, list) and series:
                    nums = [safe_float(x.get("value") if isinstance(x, dict) else x) for x in series]
                    if nums:
                        add(t, sum(nums) / len(nums))

        return out

    def fetch_news_features(self, tickers: List[str]) -> FeedResult:
        if not self.seren:
            return FeedResult(ok=False, data={}, error="SEREN_API_KEY missing")

        out: Dict[str, Dict[str, Any]] = {"_source": "exa"}
        errors = []
        source = "exa"
        for t in tickers:
            prompt = f"List short-term bearish and bullish catalysts for {t} as a SaaS stock affected by AI disruption."
            text = ""
            try:
                resp = self.seren.call_publisher(
                    "perplexity",
                    method="POST",
                    path="/chat/completions",
                    body={
                        "model": "sonar",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 180,
                        "temperature": 0.1,
                    },
                    timeout=45,
                )
                body = self.seren.unwrap_body(resp)
                text = self.extract_text(body)
                source = "perplexity"
            except Exception as exc:
                errors.append(str(exc))
                try:
                    resp = self.seren.call_publisher("exa", method="POST", path="/answer", body={"query": prompt}, timeout=45)
                    body = self.seren.unwrap_body(resp)
                    text = self.extract_text(body)
                    source = "exa"
                except Exception as exc2:
                    errors.append(str(exc2))
                    text = ""

            score = self.news_sentiment_score(text)
            out[t] = {"news_score": score, "source": source, "headline": (text[:140] if text else "")}

        out["_source"] = source
        ok = any((out.get(t, {}).get("headline") or out.get(t, {}).get("news_score", 0) > 0) for t in tickers)
        return FeedResult(ok=ok, data=out, error="; ".join(errors[-3:]) if errors else "")

    def extract_text(self, body: Any) -> str:
        if isinstance(body, str):
            return body
        if isinstance(body, dict):
            if isinstance(body.get("answer"), str):
                return body["answer"]
            if isinstance(body.get("text"), str):
                return body["text"]
            choices = body.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message", {})
                if isinstance(msg.get("content"), str):
                    return msg["content"]
            if isinstance(body.get("output"), list):
                parts = []
                for item in body["output"]:
                    for content in item.get("content", []):
                        if isinstance(content.get("text"), str):
                            parts.append(content["text"])
                if parts:
                    return "\n".join(parts)
        return ""

    def news_sentiment_score(self, text: str) -> float:
        if not text:
            return 2.5
        t = text.lower()
        bearish_words = ["downgrade", "guidance cut", "layoff", "churn", "margin pressure", "competitive threat", "lawsuit"]
        bullish_words = ["upgrade", "beat", "raised guidance", "expansion", "strong demand", "record revenue"]
        bearish = sum(t.count(w) for w in bearish_words)
        bullish = sum(t.count(w) for w in bullish_words)
        raw = 2.5 + (bearish * 0.4) - (bullish * 0.3)
        return clamp(raw, 0.0, 5.0)

    def fetch_market_features(self, tickers: List[str]) -> FeedResult:
        if not self.seren:
            return FeedResult(ok=False, data={}, error="SEREN_API_KEY missing")

        data: Dict[str, Dict[str, Any]] = {}
        errors: List[str] = []
        for i in range(0, len(tickers), 20):
            chunk = tickers[i : i + 20]
            symbols = ",".join(chunk)
            try:
                resp = self.seren.call_publisher(
                    "alpaca",
                    method="GET",
                    path=f"/v2/stocks/snapshots?symbols={symbols}",
                    timeout=45,
                )
                body = self.seren.unwrap_body(resp)
                parsed = self.parse_snapshots(body)
                for t in chunk:
                    if t in parsed:
                        data[t] = parsed[t]
            except Exception as exc:
                errors.append(str(exc))

        # backfill minimal defaults to keep engine deterministic.
        for t in tickers:
            if t not in data:
                data[t] = {
                    "price": 50.0,
                    "return_1d": 0.0,
                    "adv_usd": 5_000_000.0,
                    "shortable": True,
                    "shortable_source": "default-fallback",
                }

        ok = len(data) > 0
        return FeedResult(ok=ok, data=data, error="; ".join(errors[-3:]) if errors else "")

    def parse_snapshots(self, body: Any) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if not isinstance(body, dict):
            return out

        snapshots = body.get("snapshots") or body.get("data") or body
        if not isinstance(snapshots, dict):
            return out

        for ticker, snap in snapshots.items():
            if not isinstance(snap, dict):
                continue
            daily = snap.get("dailyBar") or {}
            prev = snap.get("prevDailyBar") or {}
            close = safe_float(daily.get("c") or daily.get("close"), 0.0)
            open_px = safe_float(daily.get("o") or daily.get("open"), close)
            volume = safe_float(prev.get("v") or prev.get("volume"), 0.0)
            prev_close = safe_float(prev.get("c") or prev.get("close"), close if close > 0 else open_px)
            ret = 0.0 if prev_close <= 0 else (close - prev_close) / prev_close
            adv_usd = volume * max(prev_close, 1.0)
            shortable = close >= 5.0 and adv_usd >= 1_000_000.0
            out[ticker.upper()] = {
                "price": close if close > 0 else max(open_px, 1.0),
                "return_1d": ret,
                "adv_usd": adv_usd,
                "shortable": shortable,
                "shortable_source": "alpaca_proxy_from_liquidity_and_price",
            }
        return out

    def score_universe(
        self,
        universe: List[str],
        sec_data: Dict[str, Dict[str, Any]],
        trends_data: Dict[str, Dict[str, Any]],
        news_data: Dict[str, Dict[str, Any]],
        market_data: Dict[str, Dict[str, Any]],
        min_conviction: float,
        max_names_orders: int,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for t in universe:
            sec = sec_data.get(t, {})
            trends = trends_data.get(t, {})
            news = news_data.get(t, {})
            market = market_data.get(t, {})

            guidance = safe_float(sec.get("guidance_mentions"))
            competition = safe_float(sec.get("competition_mentions"))
            ai_mentions = safe_float(sec.get("ai_mentions"))
            churn = safe_float(sec.get("churn_mentions"))
            filing_count = safe_float(sec.get("filing_count"))

            avg_interest = safe_float(trends.get("avg_interest"), 0.0)
            news_score = safe_float(news.get("news_score"), 2.5)

            price = safe_float(market.get("price"), 50.0)
            adv = safe_float(market.get("adv_usd"), 5_000_000.0)
            ret_1d = safe_float(market.get("return_1d"), 0.0)
            shortable = bool(market.get("shortable", True))

            f = clamp(1.5 + (guidance * 0.45) + (churn * 0.50) + (competition * 0.30) + min(1.0, filing_count / 20), 0.0, 5.0)
            trend_inverse = clamp((20.0 - avg_interest) / 20.0, 0.0, 1.0)
            a = clamp(1.2 + (ai_mentions * 0.40) + (trend_inverse * 2.2), 0.0, 5.0)
            s = clamp(news_score, 0.0, 5.0)
            liquidity = 2.5 if adv >= 20_000_000 else (2.0 if adv >= 5_000_000 else 1.2)
            technical = clamp(2.2 + (-ret_1d * 40.0), 0.0, 5.0)
            t_score = clamp((liquidity * 0.45) + (technical * 0.55), 0.0, 5.0)
            p = 0.0
            if not shortable:
                p -= 5.0
            elif adv < 3_000_000:
                p -= 0.5

            conviction = 20.0 * ((WEIGHTS["f"] * f) + (WEIGHTS["a"] * a) + (WEIGHTS["s"] * s) + (WEIGHTS["t"] * t_score) + (WEIGHTS["p"] * p))
            conviction = clamp(conviction, 0.0, 100.0)

            rows.append(
                {
                    "ticker": t,
                    "f": round(f, 2),
                    "a": round(a, 2),
                    "s": round(s, 2),
                    "t": round(t_score, 2),
                    "p": round(p, 2),
                    "conviction_0_100": round(conviction, 2),
                    "selected": False,
                    "rank_no": None,
                    "latest_filing_date": sec.get("latest_filing_date"),
                    "latest_filing_type": sec.get("latest_filing_type"),
                    "evidence_sec": {
                        "source": "sec-filings-intelligence",
                        "latest_filing_date": sec.get("latest_filing_date"),
                        "latest_filing_type": sec.get("latest_filing_type"),
                        "guidance_mentions": guidance,
                        "competition_mentions": competition,
                        "ai_mentions": ai_mentions,
                        "churn_mentions": churn,
                    },
                    "evidence_news": {
                        "source": news.get("source", "exa"),
                        "news_score": news_score,
                        "headline": news.get("headline", ""),
                    },
                    "evidence_trends": {"source": trends.get("source", "google-trends"), "avg_interest": avg_interest, "ai_anchor": "chatgpt"},
                    "catalyst_type": "guidance-update" if guidance > 0 else "earnings",
                    "catalyst_date": sec.get("latest_filing_date"),
                    "catalyst_bias": "bearish",
                    "catalyst_confidence": "MED" if conviction >= 70 else "LOW",
                    "catalyst_note": "AI compression + weakening fundamentals",
                    "_market_price": price,
                    "_shortable": shortable,
                    "_shortable_source": market.get("shortable_source", "proxy"),
                }
            )

        rows.sort(key=lambda x: x["conviction_0_100"], reverse=True)
        selected_count = 0
        for idx, r in enumerate(rows, start=1):
            r["rank_no"] = idx
            if selected_count < max_names_orders and r["conviction_0_100"] >= min_conviction and r.get("_shortable", True):
                r["selected"] = True
                selected_count += 1
        return rows

    def build_orders(self, selected_rows: List[Dict[str, Any]], portfolio_notional_usd: float) -> List[Dict[str, Any]]:
        orders: List[Dict[str, Any]] = []
        if not selected_rows:
            return orders

        weights = []
        for i, _ in enumerate(selected_rows):
            if i == 0:
                weights.append(15.0)
            elif i == 1:
                weights.append(13.0)
            else:
                weights.append(12.0)
        weight_total = sum(weights)
        scale = 100.0 / weight_total if weight_total > 0 else 1.0
        weights = [round(w * scale, 4) for w in weights]

        for row, weight in zip(selected_rows, weights):
            ticker = row["ticker"]
            price = safe_float(row.get("_market_price"), 0.0)
            if price <= 0:
                # fallback deterministic price proxy by rank
                price = 25.0 + (row["rank_no"] * 7.5)
            notional = portfolio_notional_usd * (weight / 100.0)
            qty = notional / max(price, 1.0)
            stop = price * 1.08
            target = price * 0.85
            orders.append(
                {
                    "order_ref": f"{ticker}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
                    "ticker": ticker,
                    "side": "SELL",
                    "order_type": "limit",
                    "status": "planned",
                    "qty": round(qty, 6),
                    "limit_price": round(price, 6),
                    "stop_price": round(stop, 6),
                    "filled_qty": None,
                    "filled_avg_price": None,
                    "is_simulated": True,
                    "details": {
                        "conviction_0_100": row["conviction_0_100"],
                        "planned_notional_usd": round(notional, 2),
                        "entry_price": round(price, 6),
                        "stop_price": round(stop, 6),
                        "target_price": round(target, 6),
                        "weight_pct": round(weight, 2),
                        "shortable": bool(row.get("_shortable", True)),
                        "shortable_source": row.get("_shortable_source", "alpaca_proxy_from_liquidity_and_price"),
                        "sim_assumptions": {"slippage_bps": 15, "borrow_rate_annual": 0.03},
                    },
                }
            )
        return orders

    def simulate(self, selected_rows: List[Dict[str, Any]], orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not orders:
            return {
                "net_pnl_5d": 0.0,
                "net_pnl_10d": 0.0,
                "net_pnl_20d": 0.0,
                "hit_rate_5d": 0.0,
                "hit_rate_10d": 0.0,
                "hit_rate_20d": 0.0,
                "max_drawdown": 0.0,
                "gross_exposure": 0.0,
                "mark_map": {},
            }

        by_ticker = {r["ticker"]: r for r in selected_rows}
        p5 = []
        p10 = []
        p20 = []
        gross = 0.0
        mark_map: Dict[str, float] = {}

        for o in orders:
            t = o["ticker"]
            c = safe_float(by_ticker.get(t, {}).get("conviction_0_100"), 65.0)
            edge = clamp((c - 65.0) / 35.0, 0.0, 1.0)
            entry = safe_float(o["details"]["entry_price"])
            qty = safe_float(o["qty"])
            notional = safe_float(o["details"]["planned_notional_usd"])
            gross += notional

            ret5 = 0.03 + (0.06 * edge)
            ret10 = 0.05 + (0.09 * edge)
            ret20 = 0.07 + (0.12 * edge)

            b5 = notional * 0.03 * (5.0 / 252.0)
            b10 = notional * 0.03 * (10.0 / 252.0)
            b20 = notional * 0.03 * (20.0 / 252.0)

            pnl5 = (notional * ret5) - b5
            pnl10 = (notional * ret10) - b10
            pnl20 = (notional * ret20) - b20

            p5.append(pnl5)
            p10.append(pnl10)
            p20.append(pnl20)

            # mark to 5D simulated level.
            mark_map[t] = entry * (1.0 - ret5)

        net5 = sum(p5)
        net10 = sum(p10)
        net20 = sum(p20)
        max_dd = max(0.0, abs(min(0.0, min(p5)))) + (0.10 * gross / 100.0)

        return {
            "net_pnl_5d": round(net5, 6),
            "net_pnl_10d": round(net10, 6),
            "net_pnl_20d": round(net20, 6),
            "hit_rate_5d": round(sum(1 for x in p5 if x > 0) / len(p5), 6),
            "hit_rate_10d": round(sum(1 for x in p10 if x > 0) / len(p10), 6),
            "hit_rate_20d": round(sum(1 for x in p20 if x > 0) / len(p20), 6),
            "max_drawdown": round(max_dd, 6),
            "gross_exposure": round(gross, 6),
            "mark_map": mark_map,
        }

    def build_marks_from_orders(self, orders: List[Dict[str, Any]], mark_map: Dict[str, float], source_run_id: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for o in orders:
            entry = safe_float(o["details"]["entry_price"])
            qty = safe_float(o["qty"])
            t = o["ticker"]
            mark = safe_float(mark_map.get(t), entry)
            unrealized = (entry - mark) * qty
            rows.append(
                {
                    "ticker": t,
                    "qty": qty,
                    "avg_entry_price": entry,
                    "mark_price": mark,
                    "market_value": qty * mark,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": unrealized,
                    "gross_exposure": abs(entry * qty),
                    "net_exposure": -abs(entry * qty),
                }
            )
        return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SaaS short strategy engine")
    parser.add_argument("--dsn", required=True, help="SerenDB connection string")
    parser.add_argument("--run-type", required=True, choices=["scan", "monitor", "post-close"], help="Execution run type")
    parser.add_argument("--mode", default="paper-sim", choices=["paper", "paper-sim", "live"])
    parser.add_argument("--strict-required-feeds", action="store_true", help="Block scan if required data feeds fail")
    parser.add_argument("--config", default="", help="Optional config JSON path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config: Dict[str, Any] = {}
    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)

    engine = StrategyEngine(
        dsn=args.dsn,
        api_key=os.getenv("SEREN_API_KEY"),
        strict_required_feeds=bool(args.strict_required_feeds or config.get("strict_required_feeds", False)),
    )
    engine.ensure_schema()

    mode = args.mode or config.get("mode", "paper-sim")
    if args.run_type == "scan":
        result = engine.run_scan(
            mode=mode,
            run_profile=config.get("run_profile", "single"),
            run_type="scan",
            universe=config.get("universe", DEFAULT_UNIVERSE),
            max_names_scored=int(config.get("max_names_scored", 30)),
            max_names_orders=int(config.get("max_names_orders", 8)),
            min_conviction=float(config.get("min_conviction", 65.0)),
            learning_mode=config.get("learning_mode", "adaptive-paper"),
        )
    elif args.run_type == "monitor":
        result = engine.run_monitor(mode=mode, run_profile=config.get("run_profile", "single"), run_type="monitor")
    else:
        result = engine.run_post_close(mode=mode, run_profile=config.get("run_profile", "single"))

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
