"""Agent 1: Portfolio & Execution Journal Agent.

Maintains trade ledger, tracks account state, and generates daily investor
briefings and execution journals in Markdown format.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from config.settings import settings
from agents.agent2_strategy import StrategyState, OrderPlan


class DailyExecutionRecord(BaseModel):
    """Daily execution snapshot record saved in JSON database."""
    date: str
    cycle: int
    ticker: str
    prev_close: float
    current_close: float
    T_before: float
    T_after: float
    mode: str
    
    total_shares_before: int
    total_shares_after: int
    avg_price_before: float
    avg_price_after: float
    
    remaining_cash_before: float
    remaining_cash_after: float
    strategy_shares_cycle: Optional[int] = None
    broker_cash_usd: Optional[float] = None
    broker_soxl_shares: Optional[int] = None
    broker_soxl_avg_price: Optional[float] = None
    settled_order_ids: List[str] = Field(default_factory=list)
    
    bought_shares: int = 0
    bought_amount: float = 0.0
    sold_shares: int = 0
    sold_amount: float = 0.0
    realized_pnl: float = 0.0
    
    unrealized_pnl: float = 0.0
    unrealized_return_pct: float = 0.0
    total_equity: float = 0.0
    
    investment_rationale: str = ""
    notes: List[str] = Field(default_factory=list)


class ExecutionJournalAgent:
    """Agent 1: Maintains investment journal and historical portfolio ledger."""

    def __init__(self, history_file: Optional[Path] = None, reports_dir: Optional[Path] = None):
        self.history_file = history_file or settings.history_file
        self.reports_dir = reports_dir or settings.reports_dir
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def load_history(self) -> List[Dict[str, Any]]:
        """Load historical trade records from JSON file."""
        if not self.history_file.exists():
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def save_record(self, record: DailyExecutionRecord) -> None:
        """Append or update daily execution record in JSON file."""
        records = self.load_history()
        # Remove existing record with same date if updating
        records = [r for r in records if r.get("date") != record.date]
        records.append(record.model_dump())
        # Sort by date ascending
        records.sort(key=lambda x: x.get("date", ""))
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)

    def generate_daily_markdown_report(
        self,
        record: DailyExecutionRecord,
        order_plan: Optional[OrderPlan] = None,
        macro_summary: str = "",
        semi_summary: str = "",
        fundamental_summary: str = "",
        risk_summary: str = "",
    ) -> str:
        """Generate a comprehensive daily investment report in GitHub Flavored Markdown."""
        date_str = record.date
        pnl_badge = "🟢" if record.unrealized_pnl >= 0 else "🔴"
        realized_badge = "💰" if record.realized_pnl > 0 else "➖"

        report_lines = [
            f"# 📊 [SOXL 무한매수법 V4.0] 일일 투자 일지 & 계좌 리포트",
            f"> **기준일자**: {date_str} | **종목**: `{record.ticker}` | **회차**: #{record.cycle} | **모드**: `{record.mode}`",
            "",
            "## 1. 💼 계좌 및 자산 현황 요약",
            "| 항목 | 이전 상태 | 당일 체결 후 | 변동 |",
            "|:---|:---:|:---:|:---:|",
            f"| **진행도 (T값)** | {record.T_before:.2f} / 40 | **{record.T_after:.2f} / 40** | {record.T_after - record.T_before:+.2f} |",
            f"| **보유 주식 수** | {record.total_shares_before}주 | **{record.total_shares_after}주** | {record.total_shares_after - record.total_shares_before:+d}주 |",
            f"| **평균 매수가 (평단)** | ${record.avg_price_before:.2f} | **${record.avg_price_after:.2f}** | ${record.avg_price_after - record.avg_price_before:+.2f} |",
            f"| **현재 종가** | - | **${record.current_close:.2f}** | {((record.current_close - record.prev_close)/record.prev_close)*100:+.2f}% |",
            f"| **평가 손익** | - | **{pnl_badge} ${record.unrealized_pnl:+.2f} ({record.unrealized_return_pct:+.2f}%)** | - |",
            f"| **전략 주문 현금 (USD)** | ${record.remaining_cash_before:.2f} | **${record.remaining_cash_after:.2f}** | ${record.remaining_cash_after - record.remaining_cash_before:+.2f} |",
            f"| **총 계좌 평가액** | - | **${record.total_equity:.2f}** | - |",
            f"| **실현 손익 (당일)** | - | **{realized_badge} ${record.realized_pnl:+.2f}** | - |",
            "",
            "## 2. 📝 당일 체결 내역 및 투자 근거",
            f"- **매수 체결**: {record.bought_shares}주 (${record.bought_amount:.2f})",
            f"- **매도 체결**: {record.sold_shares}주 (${record.sold_amount:.2f})",
            f"- **투자 집행 근거**: {record.investment_rationale}",
            "",
        ]
        if record.broker_soxl_shares is not None:
            report_lines.insert(
                8,
                f"| **토스 실제 SOXL 보유량** | - | **{record.broker_soxl_shares}주** | - |",
            )
        if record.broker_soxl_avg_price is not None:
            report_lines.insert(
                9,
                f"| **토스 실제 SOXL 평단** | - | **${record.broker_soxl_avg_price:.4f}** | - |",
            )
        if record.broker_cash_usd is not None:
            report_lines.insert(
                13,
                f"| **토스 USD 매수 가능 금액** | - | **${record.broker_cash_usd:.2f}** | - |",
            )

        if record.notes:
            report_lines.append("### 📌 추가 메모")
            for n in record.notes:
                report_lines.append(f"- {n}")
            report_lines.append("")

        if risk_summary:
            report_lines.extend([
                "## 3. 🛡️ 리스크 & 포지션 진단 (Agent 3)",
                risk_summary,
                "",
            ])

        if macro_summary or semi_summary or fundamental_summary:
            report_lines.extend([
                "## 4. 🌐 시장 환경 및 반도체 기업 분석 (Agents 4-6)",
                f"### 📈 거시경제 & 유동성:\n{macro_summary}" if macro_summary else "",
                f"### ⚡ 반도체 업황 기술 지표 (Agent 5):\n{semi_summary}" if semi_summary else "",
                (
                    f"### 🧾 SOXL 관련 기업 및 실적 캘린더 (Agent 6):\n{fundamental_summary}"
                    if fundamental_summary
                    else ""
                ),
                "",
            ])

        if order_plan:
            report_lines.extend([
                "## 5. 🎯 다음 거래일 주문표 (Agent 2)",
                order_plan.to_markdown_table(),
                "",
            ])

        report_lines.extend([
            "---",
            f"*Auto-generated by Auto Stock Multi-Agent System on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        ])

        markdown_content = "\n".join(report_lines)

        # Save to reports directory
        report_file = self.reports_dir / f"{date_str}_{record.ticker}_report.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        return markdown_content
