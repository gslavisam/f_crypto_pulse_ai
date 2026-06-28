from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd
import yfinance as yf

DEFAULT_INSTRUMENTS = ["MES", "AAPL", "NVDA", "MSFT", "AMZN", "META", "GOOGL"]
SESSION_START = time(9, 30)
SESSION_END = time(16, 0)
SESSION_TZ = "America/New_York"
DEFAULT_INTERVAL = "15m"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch RTH session OHLC data and export open/close plus optional VAH/VAL/POC values."
    )
    parser.add_argument("--start-date", default="2026-06-01", help="YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD (defaults to today)")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL, choices=["1m", "5m", "15m"], help="Intraday interval (15m is most reliable for this use case)")
    parser.add_argument(
        "--instruments",
        nargs="+",
        default=DEFAULT_INSTRUMENTS,
        help="Tickers to fetch. Use MES for Micro E-mini S&P 500 futures.",
    )
    parser.add_argument(
        "--output",
        default="rth_historical_data.csv",
        help="Output CSV or JSON file path (extension .csv/.json).",
    )
    return parser.parse_args()


def resolve_ticker(symbol: str) -> str:
    cleaned = (symbol or "").strip().upper()
    if cleaned == "MES":
        return "MES=F"
    return cleaned


def normalize_index_timezone(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    index = frame.index
    if getattr(index, "tz", None) is None:
        frame = frame.copy()
        frame.index = index.tz_localize("UTC")
    else:
        frame = frame.copy()
        frame.index = frame.index.tz_convert(SESSION_TZ)
    return frame


def filter_rth(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    frame = normalize_index_timezone(frame)
    mask = frame.index.time >= SESSION_START
    mask &= frame.index.time <= SESSION_END
    return frame.loc[mask]


def compute_session_summary(symbol: str, frame: pd.DataFrame) -> Dict[str, Any]:
    if frame.empty:
        return {}

    day_groups = []
    for session_date, day_frame in frame.groupby(frame.index.date):
        ordered = day_frame.sort_index()
        if ordered.empty:
            continue
        volume = ordered["Volume"].fillna(0)
        poc_index = volume.idxmax()
        typical_price = (ordered["High"] + ordered["Low"] + ordered["Close"]) / 3.0
        poc_price = float(ordered.loc[poc_index, "Close"])
        day_groups.append(
            {
                "date": session_date.strftime("%Y-%m-%d"),
                "open": float(ordered.iloc[0]["Open"]),
                "close": float(ordered.iloc[-1]["Close"]),
                "high": float(ordered["High"].max()),
                "low": float(ordered["Low"].min()),
                "vah": float(ordered["High"].max()),
                "val": float(ordered["Low"].min()),
                "poc": poc_price,
                "volume": int(volume.sum()),
                "session_start_local": "09:30",
                "session_end_local": "16:00",
            }
        )

    return {
        "instrument": symbol,
        "rows": day_groups,
    }


def fetch_rth_data(symbol: str, start_date: str, end_date: str | None, interval: str) -> List[Dict[str, Any]]:
    yfinance_symbol = resolve_ticker(symbol)
    end_dt = end_date or datetime.now().strftime("%Y-%m-%d")

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt_obj = datetime.strptime(end_dt, "%Y-%m-%d")
    collected_frames: List[pd.DataFrame] = []

    current_start = start_dt
    while current_start <= end_dt_obj:
        current_end = min(current_start + pd.Timedelta(days=30), end_dt_obj)
        current_end_str = current_end.strftime("%Y-%m-%d")
        try:
            chunk = yf.Ticker(yfinance_symbol).history(
                start=current_start.strftime("%Y-%m-%d"),
                end=current_end_str,
                interval=interval,
                auto_adjust=False,
                prepost=False,
                actions=False,
                rounding=False,
            )
        except Exception:
            chunk = pd.DataFrame()

        if not chunk.empty:
            collected_frames.append(chunk)
        current_start = current_end + pd.Timedelta(days=1)

    if not collected_frames:
        return []

    history = pd.concat(collected_frames).sort_index() if len(collected_frames) > 1 else collected_frames[0]
    if history.empty:
        return []

    session_data = filter_rth(history)
    if session_data.empty:
        return []

    summary = compute_session_summary(symbol, session_data)
    return [
        {
            "instrument": summary.get("instrument", symbol),
            "ticker": yfinance_symbol,
            **row,
        }
        for row in summary.get("rows", [])
    ]


def export_rows(rows: Iterable[Dict[str, Any]], output_path: str) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".json":
        output.write_text(json.dumps(list(rows), indent=2), encoding="utf-8")
    else:
        frame = pd.DataFrame(list(rows))
        frame = frame[[
            "date",
            "instrument",
            "ticker",
            "open",
            "close",
            "high",
            "low",
            "vah",
            "val",
            "poc",
            "volume",
            "session_start_local",
            "session_end_local",
        ]]
        frame.to_csv(output, index=False)
    return output


def main() -> None:
    args = parse_args()
    all_rows: List[Dict[str, Any]] = []
    for symbol in args.instruments:
        rows = fetch_rth_data(symbol, args.start_date, args.end_date, args.interval)
        if rows:
            all_rows.extend(rows)
        else:
            print(f"No RTH data returned for {symbol}")

    if not all_rows:
        raise SystemExit("No data was fetched. Check the ticker symbols or network connectivity.")

    output_path = export_rows(all_rows, args.output)
    print(f"Saved {len(all_rows)} rows to {output_path}")
    print(pd.DataFrame(all_rows).head(10).to_string(index=False))


if __name__ == "__main__":
    main()
