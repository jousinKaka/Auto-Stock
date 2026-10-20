"""Auto Stock: 7-Agent Infinite Buying Automation System.

Orchestrates all 7 agents for US Stock (SOXL) automated infinite buying V4.0,
macro & fundamental intelligence, risk watchdog, and Toss OpenAPI execution.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yfinance as yf
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agents import (
    ConstituentsFundamentalAgent,
    DailyExecutionRecord,
    ExecutionJournalAgent,
    InfiniteBacktester,
    InfiniteStrategyAgent,
    MacroIntelligenceAgent,
    OrderPlan,
    OrderSide,
    RiskAssessment,
    RiskLevel,
    RiskWatchdogAgent,
    SemiIndustryAgent,
    StrategyState,
    TossExecutionAgent,
)
from config.settings import settings
from services.market_data import extract_daily_closes

if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(force_terminal=True)


def print_banner():
    banner = """
    +===================================================================+
    |       🚀 AUTO STOCK: SOXL 무한매수법 V4.0 멀티 에이전트 시스템       |
    |   Laoer Infinite Buying 4.0 & Toss Securities OpenAPI Automation  |
    +===================================================================+
    """
    console.print(banner, style="bold cyan")


def run_pipeline(dry_run: bool = True) -> None:
    """Execute the full 7-agent automated trading pipeline."""
    print_banner()
    today_str = datetime.now().strftime("%Y-%m-%d")
    console.print(f"[bold yellow]▶ [1/6] 시장 인텔리전스 수집 시작 (Agents 4, 5, 6)...[/bold yellow]")

    # 1. Market Intelligence (Agents 4, 5, 6)
    macro_agent = MacroIntelligenceAgent()
    macro_res = macro_agent.fetch_and_analyze()

    semi_agent = SemiIndustryAgent()
    semi_res = semi_agent.analyze()

    fund_agent = ConstituentsFundamentalAgent()
    fund_res = fund_agent.analyze()

    console.print(f"  ✔ [green]거시 매크로 국면[/green]: {macro_res.overall_regime} (VIX: {macro_res.vix_level})")
    console.print(f"  ✔ [green]반도체 사이클[/green]: {semi_res.trend_state} (SOXX: ${semi_res.soxx_price:.2f})")
    console.print(f"  ✔ [green]편입 상위 비중[/green]: Top 3사 집중도 {fund_res.top_3_concentration_pct:.1f}%")

    # 2. Get Current/Previous Price of Target Ticker (SOXL)
    console.print(f"\n[bold yellow]▶ [2/6] {settings.target_ticker} 시세 및 계좌 상태 동기화 (Agent 7)...[/bold yellow]")
    current_close = 0.0

    try:
        soxl_data = yf.download(settings.target_ticker, period="10d", interval="1d", progress=False)
        recent_5d = extract_daily_closes(soxl_data)
    except Exception as e:
        raise RuntimeError("Unable to retrieve a reliable completed SOXL daily close.") from e

    prev_close = recent_5d[-1]
    current_close = prev_close

    # 3. Strategy State Loading & Sync
    journal_agent = ExecutionJournalAgent()
    history = journal_agent.load_history()

    # Restore latest state from history if available
    if history:
        latest = history[-1]
        state = StrategyState(
            ticker=latest.get("ticker", settings.target_ticker),
            cycle=latest.get("cycle", 1),
            divisions=settings.divisions,
            target_profit_rate=settings.target_profit_rate,
            large_number_ratio=settings.large_number_ratio,
            T=latest.get("T_after", 0.0),
            avg_price=latest.get("avg_price_after", 0.0),
            total_shares=latest.get("total_shares_after", 0),
            remaining_cash=latest.get("remaining_cash_after", settings.total_budget_usd),
            initial_budget=settings.total_budget_usd,
            mode=latest.get("mode", "NORMAL"),
        )
    else:
        state = StrategyState(
            ticker=settings.target_ticker,
            divisions=settings.divisions,
            target_profit_rate=settings.target_profit_rate,
            large_number_ratio=settings.large_number_ratio,
            remaining_cash=settings.total_budget_usd,
            initial_budget=settings.total_budget_usd,
        )

    exec_agent = TossExecutionAgent(dry_run=dry_run)
    state, sync_info = exec_agent.sync_account_state(state)
    console.print(f"  ✔ [green]계좌 상태[/green]: {sync_info.get('note', '')}")
    console.print(f"    - 보유수량: {state.total_shares}주 | 평단: ${state.avg_price:.2f} | 잔여예수금: ${state.remaining_cash:.2f}")

    # 4. Strategy Engine: Generate Today's Orders (Agent 2)
    console.print(f"\n[bold yellow]▶ [3/6] 무한매수법 V4.0 주문표 산출 (Agent 2)...[/bold yellow]")
    strat_agent = InfiniteStrategyAgent(state)
    order_plan = strat_agent.generate_daily_orders(
        prev_close=prev_close,
        recent_5d_closes=recent_5d,
        date_str=today_str,
    )

    # Render Order Sheet in Console
    order_table = Table(title=f"📋 오늘의 {settings.target_ticker} 주문표 (T값: {state.T:.2f}/{state.divisions})", show_lines=True)
    order_table.add_column("구분", style="bold", justify="center")
    order_table.add_column("유형", justify="center")
    order_table.add_column("가격($)", justify="right")
    order_table.add_column("수량", justify="right")
    order_table.add_column("예상금액($)", justify="right")
    order_table.add_column("설명", style="dim")

    for o in order_plan.orders:
        side_style = "red" if o.side == OrderSide.BUY else "blue"
        side_text = "매수" if o.side == OrderSide.BUY else "매도"
        order_table.add_row(
            f"[{side_style}]{side_text}[/{side_style}]",
            f"`{o.order_type.value}`",
            f"${o.price:.2f}",
            f"{o.quantity}주",
            f"${o.estimated_total:.2f}",
            o.description,
        )
    console.print(order_table)

    # 5. Risk Assessment (Agent 3)
    console.print(f"\n[bold yellow]▶ [4/6] 변동성 끌림 & 리스크 진단 (Agent 3)...[/bold yellow]")
    risk_agent = RiskWatchdogAgent(target_divisions=settings.divisions)
    risk_res = risk_agent.assess_risk(state, curr_price=current_close, recent_daily_closes=recent_5d)
    
    risk_panel_style = {
        RiskLevel.GREEN: "green",
        RiskLevel.YELLOW: "yellow",
        RiskLevel.ORANGE: "orange1",
        RiskLevel.RED: "red",
    }[risk_res.risk_level]
    console.print(Panel(risk_res.to_markdown(), title="🛡️ 리스크 진단 결과", border_style=risk_panel_style))

    # 6. Broker Execution (Agent 7)
    mode_label = "모의실행 (Dry-Run)" if dry_run else "실거래 (Live)"
    console.print(f"\n[bold yellow]▶ [5/6] 토스증권 {mode_label} 주문 접수 (Agent 7)...[/bold yellow]")
    exec_result = exec_agent.execute_order_plan(order_plan)
    console.print(f"  ✔ [bold]{exec_result.summary_message}[/bold]")

    # 7. Journaling & Daily Report (Agent 1)
    console.print(f"\n[bold yellow]▶ [6/6] 투자 일지 및 데일리 리포트 생성 (Agent 1)...[/bold yellow]")
    total_equity = state.remaining_cash + (state.total_shares * current_close)
    unrealized_pnl = (current_close - state.avg_price) * state.total_shares if state.avg_price > 0 else 0.0
    unrealized_pct = (unrealized_pnl / (state.avg_price * state.total_shares)) * 100.0 if state.total_shares > 0 else 0.0

    record = DailyExecutionRecord(
        date=today_str,
        cycle=state.cycle,
        ticker=state.ticker,
        prev_close=prev_close,
        current_close=current_close,
        T_before=state.T,
        T_after=state.T,
        mode=state.mode.value,
        total_shares_before=state.total_shares,
        total_shares_after=state.total_shares,
        avg_price_before=state.avg_price,
        avg_price_after=state.avg_price,
        remaining_cash_before=state.remaining_cash,
        remaining_cash_after=state.remaining_cash,
        unrealized_pnl=round(unrealized_pnl, 2),
        unrealized_return_pct=round(unrealized_pct, 2),
        total_equity=round(total_equity, 2),
        investment_rationale=(
            f"무한매수법 V4.0 {state.divisions}분할 전략에 의거하여 "
            f"T값 {state.T:.2f} 기준 {'전반전' if state.is_first_half else '후반전'} 주문표를 생성 및 접수함."
        ),
        notes=[
            f"매크로 국면: {macro_res.overall_regime}",
            f"반도체 사이클: {semi_res.trend_state}",
            f"위험 등급: {risk_res.risk_level.value}",
        ],
    )
    journal_agent.save_record(record)
    report_md = journal_agent.generate_daily_markdown_report(
        record=record,
        order_plan=order_plan,
        macro_summary=macro_res.to_markdown(),
        semi_summary=semi_res.to_markdown(),
        fundamental_summary=fund_res.to_markdown(),
        risk_summary=risk_res.to_markdown(),
    )

    report_path = journal_agent.reports_dir / f"{today_str}_{settings.target_ticker}_report.md"
    console.print(f"  ✔ [green]일일 리포트 저장 완료[/green]: [link=file:///{report_path}]{report_path}[/link]")
    console.print("\n[bold green]✨ 모든 에이전트 파이프라인이 성공적으로 완료되었습니다![/bold green]\n")


def run_backtest(years: int = 5) -> None:
    """Run historical backtest for SOXL infinite buying V4.0."""
    print_banner()
    console.print(f"[bold yellow]▶ SOXL 무한매수법 V4.0 과거 {years}년 백테스트 시작...[/bold yellow]")
    
    try:
        df = yf.download("SOXL", period=f"{years}y", interval="1d", progress=False)
        if df.empty:
            console.print("[red]SOXL 시세 데이터를 다운로드할 수 없습니다.[/red]")
            return

        backtester = InfiniteBacktester(
            ticker="SOXL",
            divisions=settings.divisions,
            target_profit_rate=settings.target_profit_rate,
            initial_capital=settings.total_budget_usd,
        )
        res = backtester.run(df)

        table = Table(title="📊 무한매수법 V4.0 백테스트 성과 지표", show_lines=True)
        table.add_column("항목", style="bold cyan")
        table.add_column("결과 수치", justify="right", style="bold green")

        table.add_row("초기 투자 원금", f"${res['initial_capital']:,.2f}")
        table.add_row("최종 계좌 평가액", f"${res['final_equity']:,.2f}")
        table.add_row("총 누적 수익률", f"{res['total_return_pct']:+.2f}%")
        table.add_row("최대 낙폭 (MDD)", f"{res['max_drawdown_pct']:.2f}%")
        table.add_row("완료된 총 사이클 수", f"{res['total_cycles_completed']}회")

        console.print(table)
    except Exception as e:
        console.print(f"[red]백테스트 실행 중 오류 발생: {e}[/red]")


def main():
    parser = argparse.ArgumentParser(description="Auto Stock SOXL Infinite Buying System")
    parser.add_argument("--mode", choices=["run", "backtest"], default="run", help="Execution mode")
    parser.add_argument("--live", action="store_true", help="Execute live orders on Toss OpenAPI")
    parser.add_argument("--years", type=int, default=3, help="Backtest years")
    args = parser.parse_args()

    if args.mode == "run":
        run_pipeline(dry_run=not args.live)
    elif args.mode == "backtest":
        run_backtest(years=args.years)


if __name__ == "__main__":
    main()
