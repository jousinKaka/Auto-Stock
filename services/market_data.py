from __future__ import annotations

import math
from datetime import datetime
from typing import List
from zoneinfo import ZoneInfo

import pandas as pd


def extract_daily_closes(data: pd.DataFrame, limit: int = 5) -> List[float]:
    """Return recent non-null daily closes, excluding today's in-progress bar."""
    if data.empty or "Close" not in data:
        raise ValueError("Daily price data has no Close column or rows.")

    closes = data["Close"]
    if isinstance(closes, pd.DataFrame):
        if closes.shape[1] == 0:
            raise ValueError("Daily price data has no Close values.")
        closes = closes.iloc[:, 0]

    close_series = closes.dropna()
    if len(close_series) > 0:
        last_timestamp = close_series.index[-1]
        if hasattr(last_timestamp, "date"):
            last_date = last_timestamp.date()
            if getattr(last_timestamp, "tzinfo", None) is not None:
                last_date = last_timestamp.tz_convert("America/New_York").date()
            if last_date >= datetime.now(ZoneInfo("America/New_York")).date():
                close_series = close_series.iloc[:-1]

    values = [
        float(value)
        for value in close_series.tail(limit).tolist()
        if math.isfinite(float(value)) and float(value) > 0
    ]
    if not values:
        raise ValueError("Daily price data has no valid closing prices.")
    return values
