"""Morning Routine: 09:00 AM Daily Automated Stock Manager.

1. Settles overnight US trade executions from Toss Securities.
2. Recalculates T-value, cash runway, average price, and realized/unrealized PnL.
3. Gathers fresh macro & semiconductor technical intelligence.
4. Generates today's 무한매수 V4.0 to-do order sheet (OrderPlan).
5. Places or stages today's orders via Toss OpenAPI.
6. Saves comprehensive Markdown investment journal.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agents import (
    ConstituentsFundamentalAgent,
    DailyExecutionRecord,
    ExecutionJournalAgent,
    InfiniteStrategyAgent,
    MacroIntelligenceAgent,
    OrderPlan,
    OrderSide,
    RiskAssessment,
    RiskLevel,
    RiskWatchdogAgent,
    SemiIndustryAgent,
    StrategyMode,
    StrategyState,
    TossExecutionAgent,
)
from config.settings import settings
from config.toss_config import toss_config
from services.toss_client import TossSecuritiesClient

if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(force_terminal=True)


def run_morning_routine(live_order: bool = False) -> None:
    today_str = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    console.print(f"\n[bold cyan]🌅 [09:00 AM 모닝 루틴 시작] SOXL 무한매수 V4.0 자동화 ({now_str})[/bold cyan]\n")

    journal_agent = ExecutionJournalAgent()
    history = journal_agent.load_history()

    # 1. Load Last Recorded State
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
        )
    else:
        # Initial default or mid-entry state
        state = StrategyState(
            ticker=settings.target_ticker,
            divisions=settings.divisions,
            target_profit_rate=settings.target_profit_rate,
            large_number_ratio=settings.large_number_ratio,
            remaining_cash=settings.total_budget_usd,
            initial_budget=settings.total_budget_usd,
        )

    # 2. Check Overnight Executions via Toss Securities API
    console.print("[bold yellow]▶ [1/4] 전일 야간 미국장 체결 결과 확인 (토스증권 API)...[/bold yellow]")
    client = TossSecuritiesClient()
    
    overnight_bought_shares = 0
    overnight_bought_cost = 0.0
    overnight_sold_shares = 0
    overnight_sold_revenue = 0.0
    realized_pnl = 0.0
    prev_shares = state.total_shares
    prev_avg_price = state.avg_price
    prev_T = state.T
    prev_cash = state.remaining_cash

    if client.is_configured:
        try:
            # Query live holdings & buying power
            holdings_resp = client.get_holdings()
            items = holdings_resp.get("items", [])
            soxl_item = next((it for it in items if it.get("symbol") == settings.target_ticker), None)

            current_broker_shares = int(soxl_item.get("quantity", 0)) if soxl_item else 0
            current_broker_avg = float(soxl_item.get("averagePrice", 0.0)) if soxl_item else 0.0

            bp_resp = client.get_buying_power(currency="USD")
            raw_cash = bp_resp.get("cashBuyingPower", bp_resp.get("usdCash", state.remaining_cash))
            current_broker_cash = float(raw_cash) if raw_cash is not None else state.remaining_cash

            # Detect overnight share delta
            if current_broker_cash > 0 or current_broker_shares > 0:
                share_delta = current_broker_shares - prev_shares
                if share_delta > 0:
                    overnight_bought_shares = share_delta
                    overnight_bought_cost = round(prev_cash - current_broker_cash, 2)
                    console.print(f"  ✔ [green]야간 매수 체결 확인:[/green] +{overnight_bought_shares}주 체결 완료 (비용: ${overnight_bought_cost:.2f})")
                    state.total_shares = current_broker_shares
                    state.avg_price = current_broker_avg
                    state.remaining_cash = current_broker_cash
                    state.T += 1.0  # Increment T
                elif share_delta < 0:
                    overnight_sold_shares = abs(share_delta)
                    overnight_sold_revenue = round(current_broker_cash - prev_cash, 2)
                    realized_pnl = round(overnight_sold_revenue - (prev_avg_price * overnight_sold_shares), 2)
                    console.print(f"  ✔ [green]야간 매도(익절/쿼터) 체결 확인:[/green] -{overnight_sold_shares}주 체결 (수익: ${realized_pnl:+.2f})")
                    state.total_shares = current_broker_shares
                    state.avg_price = current_broker_avg if current_broker_shares > 0 else 0.0
                    state.remaining_cash = current_broker_cash
                    if current_broker_shares == 0:
                        console.print("  🎉 [bold green]전량 익절 완료! 사이클이 종료되어 새 회차가 시작됩니다.[/bold green]")
                        state.cycle += 1
                        state.T = 0.0
                    else:
                        state.T *= 0.75  # Quarter sell decay
                else:
                    console.print("  ✔ [dim]전일 야간 체결 내역 없음 (보유 수량 변동 없음)[/dim]")
            else:
                # Broker account is not yet funded or holding 0, preserve external position from ledger
                console.print(f"  ✔ [dim]외부/통합 계좌 포지션 유지:[/dim] {state.total_shares}주 @ ${state.avg_price:.2f} (T값: {state.T:.2f})")
        except Exception as e:
            console.print(f"  ⚠️ [yellow]토스 API 조회 실패 (로컬 상태 유지):[/yellow] {e}")
    else:
        console.print("  [dim]API 미설정으로 로컬 장부 상태를 기준으로 진행합니다.[/dim]")

    # 3. Live Price & Market Intelligence
    console.print("\n[bold yellow]▶ [2/4] 마감 종가 및 시장 인텔리전스 수집...[/bold yellow]")
    current_close = 100.0
    recent_5d = []
    try:
        # Try Toss API first for official real-time price
        if client.is_configured:
            current_close = client.get_current_price(settings.target_ticker)
        
        # Download recent history via yfinance
        df = yf.download(settings.target_ticker, period="10d", interval="1d", progress=False)
        if not df.empty:
            closes = df["Close"].squeeze()
            if isinstance(closes, pd.DataFrame):
                closes = closes.iloc[:, 0]
            closes = closes.dropna()
            current_close = float(closes.iloc[-1]) if current_close <= 0 else current_close
            recent_5d = [float(x) for x in closes.iloc[-5:].values]
    except Exception as e:
        console.print(f"  [dim]시세 조회 오류: {e}[/dim]")

    macro_agent = MacroIntelligenceAgent()
    macro_res = macro_agent.fetch_and_analyze()

    semi_agent = SemiIndustryAgent()
    semi_res = semi_agent.analyze()

    fund_agent = ConstituentsFundamentalAgent()
    fund_res = fund_agent.analyze()

    console.print(f"  ✔ {settings.target_ticker} 마감 종가: ${current_close:.2f}")
    console.print(f"  ✔ 거시 매크로: {macro_res.overall_regime} | 반도체 사이클: {semi_res.trend_state}")

    # 4. Generate Today's To-Do Orders (Agent 2)
    console.print(f"\n[bold yellow]▶ [3/4] 오늘의 무한매수 V4.0 To-Do 주문표 산출 (Agent 2)...[/bold yellow]")
    strat_agent = InfiniteStrategyAgent(state)
    order_plan = strat_agent.generate_daily_orders(
        prev_close=current_close,
        recent_5d_closes=recent_5d,
        date_str=today_str,
    )

    order_table = Table(title=f"📋 오늘의 {settings.target_ticker} To-Do 매매 주문표 (T값: {state.T:.2f}/{state.divisions})", show_lines=True)
    order_table.add_column("구분", justify="center", style="bold")
    order_table.add_column("유형", justify="center")
    order_table.add_column("주문가격($)", justify="right")
    order_table.add_column("수량", justify="right")
    order_table.add_column("예상금액($)", justify="right")
    order_table.add_column("설명 및 목적", style="dim")

    for o in order_plan.orders:
        s_style = "red" if o.side == OrderSide.BUY else "blue"
        s_text = "매수" if o.side == OrderSide.BUY else "매도"
        order_table.add_row(
            f"[{s_style}]{s_text}[/{s_style}]",
            f"`{o.order_type.value}`",
            f"${o.price:.2f}",
            f"{o.quantity}주",
            f"${o.estimated_total:.2f}",
            o.description,
        )
    console.print(order_table)

    # 5. Risk Assessment (Agent 3)
    risk_agent = RiskWatchdogAgent(target_divisions=settings.divisions)
    risk_res = risk_agent.assess_risk(state, curr_price=current_close, recent_daily_closes=recent_5d)
    
    # 6. Execute or Stage Orders (Agent 7)
    exec_agent = TossExecutionAgent(dry_run=not live_order)
    mode_tag = "실거래 주문 접수" if live_order else "모의 접수 (Dry-Run)"
    console.print(f"\n[bold yellow]▶ [4/4] 토스증권 {mode_tag} 진행 (Agent 7)...[/bold yellow]")
    exec_result = exec_agent.execute_order_plan(order_plan)
    console.print(f"  ✔ [bold]{exec_result.summary_message}[/bold]")

    # 7. Journal & Markdown Report Creation (Agent 1)
    total_equity = state.remaining_cash + (state.total_shares * current_close)
    unrealized_pnl = (current_close - state.avg_price) * state.total_shares if state.avg_price > 0 else 0.0
    unrealized_pct = (unrealized_pnl / (state.avg_price * state.total_shares)) * 100.0 if state.total_shares > 0 else 0.0

    record = DailyExecutionRecord(
        date=today_str,
        cycle=state.cycle,
        ticker=state.ticker,
        prev_close=current_close,
        current_close=current_close,
        T_before=prev_T,
        T_after=state.T,
        mode=state.mode.value,
        total_shares_before=prev_shares,
        total_shares_after=state.total_shares,
        avg_price_before=prev_avg_price,
        avg_price_after=state.avg_price,
        remaining_cash_before=prev_cash,
        remaining_cash_after=state.remaining_cash,
        bought_shares=overnight_bought_shares,
        bought_amount=overnight_bought_cost,
        sold_shares=overnight_sold_shares,
        sold_amount=overnight_sold_revenue,
        realized_pnl=realized_pnl,
        unrealized_pnl=round(unrealized_pnl, 2),
        unrealized_return_pct=round(unrealized_pct, 2),
        total_equity=round(total_equity, 2),
        investment_rationale=(
            f"09:00 AM 정기 루틴: 전일 체결 정산 후 T값 {state.T:.2f} 기준으로 "
            f"오늘의 1회 매수금 ${order_plan.one_unit_cash:.2f} 주문표 생성 및 {mode_tag} 완료."
        ),
        notes=[
            f"야간 체결: 매수 {overnight_bought_shares}주 / 매도 {overnight_sold_shares}주",
            f"매크로: {macro_res.overall_regime} | 반도체: {semi_res.trend_state}",
            f"리스크 등급: {risk_res.risk_level.value}",
        ],
    )
    journal_agent.save_record(record)
    report_md = journal_agent.generate_daily_markdown_report(
        record=record,
        order_plan=order_plan,
        macro_summary=macro_res.to_markdown(),
        semi_summary=semi_res.to_markdown(),
        risk_summary=risk_res.to_markdown(),
    )

    report_path = journal_agent.reports_dir / f"{today_str}_{settings.target_ticker}_report.md"
    console.print(f"\n✔ [green]일일 투자 보고서 저장 완료:[/green] [link=file:///{report_path}]{report_path}[/link]")

    # 8. Send Telegram Briefing
    from services.telegram_notifier import TelegramNotifier
    tg = TelegramNotifier()
    if tg.is_configured:
        tg_ok = tg.send_morning_briefing(
            record=record,
            order_plan=order_plan,
            risk_headline=risk_res.headline,
            risk_level=risk_res.risk_level.value,
            soxx_price=semi_res.soxx_price,
            soxx_rsi=semi_res.rsi_14,
            macro_regime=macro_res.overall_regime,
        )
        if tg_ok:
            console.print("  ✔ [green]텔레그램 일일 리포트 발송 완료![/green]")
        else:
            console.print("  ⚠️ [yellow]텔레그램 발송 실패 (토큰 및 Chat ID 확인 필요)[/yellow]")
    else:
        console.print("  [dim]💡 텔레그램 알림 미설정 (.env에 토큰과 Chat ID 설정 시 매일 아침 자동 전송)[/dim]")

    console.print("[bold green]🎉 09:00 AM 모닝 루틴이 완벽하게 완료되었습니다![/bold green]\n")


def main():
    parser = argparse.ArgumentParser(description="Auto Stock 09:00 AM Daily Routine")
    parser.add_argument("--live", action="store_true", help="Submit actual orders to Toss Securities OpenAPI")
    args = parser.parse_args()
    run_morning_routine(live_order=args.live)


if __name__ == "__main__":
    main()
