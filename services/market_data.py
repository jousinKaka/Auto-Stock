from __future__ import annotations

import math
from datetime import datetime, time
from typing import List, Optional
from zoneinfo import ZoneInfo

import pandas as pd


def extract_daily_closes(
    data: pd.DataFrame,
    limit: int = 5,
    now: Optional[datetime] = None,
) -> List[float]:
    """Return recent non-null closes, including today's bar only after the US close."""
    close_series = extract_daily_close_series(data, limit=limit, now=now)
    return [float(value) for value in close_series.tolist()]


def extract_daily_close_series(
    data: pd.DataFrame,
    limit: Optional[int] = 5,
    now: Optional[datetime] = None,
) -> pd.Series:
    """Return completed, valid daily closes with their original timestamps."""
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
            if getattr(last_timestamp, "tzinfo", None) is not None:
                last_timestamp = last_timestamp.tz_convert("America/New_York")
            last_date = last_timestamp.date()
            current_time = now or datetime.now(ZoneInfo("America/New_York"))
            if current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=ZoneInfo("America/New_York"))
            else:
                current_time = current_time.astimezone(ZoneInfo("America/New_York"))
            same_day_bar_is_incomplete = (
                last_date == current_time.date()
                and current_time.time().replace(tzinfo=None) < time(16, 0)
            )
            if last_date > current_time.date() or same_day_bar_is_incomplete:
                close_series = close_series.iloc[:-1]

    valid_mask = close_series.map(
        lambda value: math.isfinite(float(value)) and float(value) > 0
    )
    close_series = close_series.loc[valid_mask]
    if limit is not None:
        close_series = close_series.tail(limit)
    if close_series.empty:
        raise ValueError("Daily price data has no valid closing prices.")
    return close_series.astype(float)
