"""Agent 6: SOXL-related semiconductor company and earnings-calendar analysis."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from pydantic import BaseModel, Field

from services.market_data import extract_daily_close_series


class ConstituentStockInfo(BaseModel):
    symbol: str
    company_name: str
    current_price: Optional[float] = None
    change_1d_pct: Optional[float] = None
    next_earnings_date: Optional[str] = None
    consensus_eps: Optional[float] = None
    consensus_revenue: Optional[float] = None
    key_metrics_to_watch: str
    data_quality_note: str = ""


class SOXLConstituentsAnalysis(BaseModel):
    timestamp: str
    data_source: str = "Yahoo Finance"
    price_data_as_of: Optional[str] = None
    companies: List[ConstituentStockInfo] = Field(default_factory=list)
    scope_note: str = (
        "분석 대상은 반도체 주요 종목 watchlist이며, SOXL의 실시간 편입 종목·비중을 뜻하지 않습니다."
    )
    data_quality_note: str = ""

    def to_markdown(self) -> str:
        lines = [
            f"- **데이터 출처/조회 시각**: {self.data_source} / {self.timestamp}",
            f"- **분석 범위**: {self.scope_note}",
        ]
        if self.price_data_as_of:
            lines.append(f"- **주가 기준 거래일**: {self.price_data_as_of} (미국 동부시간 기준 완료 일봉)")
        if self.data_quality_note:
            lines.append(f"- **주가 데이터 참고**: {self.data_quality_note}")
        lines.extend([
            "",
            "| 종목 | 기업 | 주가($) | 1일 등락 | 다음 실적일 | EPS 컨센서스 | 매출 컨센서스 | 실적 체크 지표 | 데이터 참고 | 출처 |",
            "|:---|:---|---:|---:|:---|---:|---:|:---|:---|:---|",
        ])
        for company in self.companies:
            price = f"${company.current_price:.2f}" if company.current_price is not None else "N/A"
            change = f"{company.change_1d_pct:+.2f}%" if company.change_1d_pct is not None else "N/A"
            eps = f"${company.consensus_eps:.2f}" if company.consensus_eps is not None else "N/A"
            revenue = (
                f"${company.consensus_revenue / 1_000_000_000:.2f}B"
                if company.consensus_revenue is not None
                else "N/A"
            )
            lines.append(
                f"| `{company.symbol}` | {company.company_name} | {price} | {change} | "
                f"{company.next_earnings_date or 'N/A'} | {eps} | {revenue} | "
                f"{company.key_metrics_to_watch} | {company.data_quality_note or '-'} | "
                f"[Yahoo Finance](https://finance.yahoo.com/quote/{company.symbol}/) |"
            )
        lines.extend([
            "",
            "EPS·매출은 Yahoo Finance 실적 캘린더의 평균 추정치이며, 조회 시점 이후 변경될 수 있습니다. "
            "추정치가 없으면 N/A로 표시합니다. 이 분석은 주문 가격·수량 계산에 사용되지 않습니다.",
        ])
        return "\n".join(lines)


class ConstituentsFundamentalAgent:
    """Analyze a curated semiconductor watchlist and available earnings-calendar estimates."""

    CORE_COMPANIES = [
        {
            "symbol": "NVDA",
            "name": "엔비디아",
            "metrics": "데이터센터 매출 성장, 가속기 수요, 총이익률",
        },
        {
            "symbol": "AVGO",
            "name": "브로드컴",
            "metrics": "AI 반도체 매출, 커스텀 가속기 수요, 인프라 소프트웨어",
        },
        {
            "symbol": "AMD",
            "name": "AMD",
            "metrics": "데이터센터 매출, 가속기 판매, 서버 CPU 점유 흐름",
        },
        {
            "symbol": "QCOM",
            "name": "퀄컴",
            "metrics": "핸드셋 칩 매출, 자동차·IoT 성장, 라이선스 수익",
        },
        {
            "symbol": "TSM",
            "name": "TSMC",
            "metrics": "첨단 공정 수요, HPC 매출 비중, 가동률·총이익률",
        },
        {
            "symbol": "MU",
            "name": "마이크론",
            "metrics": "DRAM·NAND 가격, HBM 공급, 재고·마진",
        },
        {
            "symbol": "AMAT",
            "name": "어플라이드 머티어리얼즈",
            "metrics": "장비 수주, 서비스 매출, 지역별 수요·수출 제한",
        },
        {
            "symbol": "INTC",
            "name": "인텔",
            "metrics": "파운드리 매출·손실, 공정 전환 진척, 데이터센터·PC 수요",
        },
    ]

    def analyze(self) -> SOXLConstituentsAnalysis:
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        symbols = [company["symbol"] for company in self.CORE_COMPANIES]
        close_table: Optional[pd.DataFrame] = None
        price_error = ""
        price_data_as_of: Optional[str] = None

        try:
            data = yf.download(symbols, period="5d", interval="1d", progress=False)
            if not data.empty and "Close" in data:
                close_data = data["Close"]
                if isinstance(close_data, pd.Series):
                    close_table = close_data.to_frame(name=symbols[0])
                elif isinstance(close_data, pd.DataFrame):
                    close_table = close_data
                if close_table is not None and not close_table.empty:
                    price_data_as_of = self._latest_completed_date(close_table, now)
            else:
                price_error = "Yahoo Finance가 종가 데이터를 반환하지 않았습니다."
        except Exception as exc:
            price_error = f"Yahoo Finance 주가 조회 실패: {type(exc).__name__}: {exc}"

        companies: List[ConstituentStockInfo] = []
        for item in self.CORE_COMPANIES:
            symbol = item["symbol"]
            current_price: Optional[float] = None
            change_1d_pct: Optional[float] = None
            quality_notes: List[str] = []

            if close_table is not None and symbol in close_table.columns:
                try:
                    closes = extract_daily_close_series(
                        pd.DataFrame({"Close": close_table[symbol]}),
                        limit=2,
                        now=now,
                    )
                    if len(closes) >= 2:
                        previous = float(closes.iloc[-2])
                        current_price = float(closes.iloc[-1])
                        change_1d_pct = (current_price - previous) / previous * 100.0
                    else:
                        quality_notes.append("완료된 주가 데이터가 2거래일 미만입니다.")
                except (KeyError, ValueError, TypeError) as exc:
                    quality_notes.append(f"주가 데이터 산출 불가: {exc}")
            else:
                quality_notes.append(price_error or "해당 종목의 종가 데이터가 없습니다.")

            earnings_date: Optional[str] = None
            consensus_eps: Optional[float] = None
            consensus_revenue: Optional[float] = None
            try:
                calendar = yf.Ticker(symbol).calendar
                raw_earnings_date = self._calendar_value(calendar, "Earnings Date")
                earnings_date = self._next_earnings_date(raw_earnings_date, now.date())
                if earnings_date is None:
                    quality_notes.append("확인 가능한 향후 실적 발표일이 없습니다.")
                else:
                    consensus_eps = self._calendar_number(
                        self._calendar_value(calendar, "Earnings Average")
                    )
                    consensus_revenue = self._calendar_number(
                        self._calendar_value(calendar, "Revenue Average")
                    )
                    if consensus_eps is None or consensus_revenue is None:
                        quality_notes.append("실적 캘린더의 EPS 또는 매출 평균 추정치가 없습니다.")
            except Exception as exc:
                quality_notes.append(
                    f"Yahoo Finance 실적 캘린더 조회 실패: {type(exc).__name__}: {exc}"
                )

            companies.append(
                ConstituentStockInfo(
                    symbol=symbol,
                    company_name=item["name"],
                    current_price=round(current_price, 2) if current_price is not None else None,
                    change_1d_pct=round(change_1d_pct, 2) if change_1d_pct is not None else None,
                    next_earnings_date=earnings_date,
                    consensus_eps=consensus_eps,
                    consensus_revenue=consensus_revenue,
                    key_metrics_to_watch=item["metrics"],
                    data_quality_note="; ".join(quality_notes),
                )
            )

        return SOXLConstituentsAnalysis(
            timestamp=now.isoformat(timespec="seconds"),
            price_data_as_of=price_data_as_of,
            companies=companies,
            data_quality_note=price_error,
        )

    @staticmethod
    def _latest_completed_date(close_table: pd.DataFrame, now: datetime) -> Optional[str]:
        for symbol in close_table.columns:
            try:
                closes = extract_daily_close_series(
                    pd.DataFrame({"Close": close_table[symbol]}),
                    limit=1,
                    now=now,
                )
                return closes.index[-1].strftime("%Y-%m-%d")
            except (KeyError, ValueError, TypeError):
                continue
        return None

    @staticmethod
    def _calendar_value(calendar: Any, key: str) -> Any:
        if isinstance(calendar, dict):
            return calendar.get(key)
        if isinstance(calendar, pd.DataFrame) and key in calendar.index:
            row = calendar.loc[key]
            if isinstance(row, pd.Series):
                return row.iloc[0] if not row.empty else None
            return row
        return None

    @staticmethod
    def _calendar_number(value: Any) -> Optional[float]:
        if isinstance(value, pd.Series):
            value = value.iloc[0] if not value.empty else None
        elif isinstance(value, (list, tuple)):
            value = value[0] if value else None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _next_earnings_date(value: Any, today: date) -> Optional[str]:
        candidates = value if isinstance(value, (list, tuple, pd.Series)) else [value]
        dates: List[date] = []
        for candidate in candidates:
            if candidate is None:
                continue
            try:
                parsed = pd.Timestamp(candidate)
            except (TypeError, ValueError):
                continue
            if pd.isna(parsed):
                continue
            if parsed.tzinfo is not None:
                parsed = parsed.tz_convert("America/New_York")
            candidate_date = parsed.date()
            if candidate_date >= today:
                dates.append(candidate_date)
        return min(dates).isoformat() if dates else None