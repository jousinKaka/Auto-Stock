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
import math
import sys
from datetime import datetime
from typing import Optional

import yfinance as yf
from rich.console import Console
from rich.table import Table

from agents import (
    ConstituentsFundamentalAgent,
    DailyExecutionRecord,
    ExecutionJournalAgent,
    InfiniteStrategyAgent,
    MacroIntelligenceAgent,
    OrderSide,
    RiskWatchdogAgent,
    SemiIndustryAgent,
    StrategyMode,
    StrategyState,
    TossExecutionAgent,
)
from config.settings import settings
from services.market_data import extract_daily_closes
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


def _sync_strategy_cash(
    state: StrategyState,
    broker_cash: float,
    cash_basis_cycle: Optional[int],
) -> Optional[int]:
    if cash_basis_cycle == state.cycle:
        modeled_cash = round(state.initial_budget - state.total_invested, 2)
        if modeled_cash < 0:
            raise RuntimeError(
                f"Cost-basis cash model is negative (${modeled_cash:.2f}); "
                "review the budget and SOXL holdings before generating orders."
            )
        state.remaining_cash = modeled_cash
        return cash_basis_cycle

    state.remaining_cash = broker_cash
    return None


def run_morning_routine(
    live_order: bool = False,
    max_live_buy_notional_usd: Optional[float] = None,
) -> None:
    today_str = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    console.print(f"\n[bold cyan]🌅 [09:00 AM 모닝 루틴 시작] SOXL 무한매수 V4.0 자동화 ({now_str})[/bold cyan]\n")

    journal_agent = ExecutionJournalAgent()
    history = journal_agent.load_history()

    # 1. Load Last Recorded State
    if history:
        latest = history[-1]
        cash_basis_cycle = latest.get("cash_basis_cycle")
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
            mode=StrategyMode(latest.get("mode", StrategyMode.NORMAL.value)),
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
        cash_basis_cycle = None

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
    broker_cash_usd: Optional[float] = None

    if client.is_configured:
        # Closed orders provide actual fill quantities, prices, and LOC/limit types;
        # a holdings delta alone cannot distinguish quarter sells from target sells.
        if history and latest.get("date", today_str) < today_str:
            cursor = None
            closed_orders = []
            while True:
                page = client.get_order_history(
                    status="CLOSED",
                    symbol=settings.target_ticker,
                    from_date=latest["date"],
                    to_date=today_str,
                    cursor=cursor,
                    limit=100,
                )
                page_orders = page.get("orders", [])
                if not isinstance(page_orders, list):
                    raise RuntimeError("Toss order-history response has an invalid orders field.")
                closed_orders.extend(page_orders)
                if not page.get("hasNext"):
                    break
                cursor = page.get("nextCursor")
                if not cursor:
                    raise RuntimeError("Toss order-history page is missing its next cursor.")

            closed_orders.sort(
                key=lambda order: (
                    (order.get("execution") or {}).get("filledAt") or "",
                    order.get("orderedAt") or "",
                )
            )
            filled_buys = []
            filled_sells = []
            for order in closed_orders:
                execution = order.get("execution") or {}
                filled_value = float(execution.get("filledQuantity") or 0)
                if filled_value <= 0:
                    continue
                quantity = int(filled_value)
                if quantity != filled_value:
                    raise ValueError(
                        f"Fractional SOXL fills are not supported by the strategy ledger: {filled_value} shares."
                    )

                avg_fill_price = execution.get("averageFilledPrice")
                if avg_fill_price is None:
                    filled_amount = execution.get("filledAmount")
                    if filled_amount is None:
                        raise RuntimeError("Toss execution record has no fill price or filled amount.")
                    fill_price = float(filled_amount) / quantity
                else:
                    fill_price = float(avg_fill_price)

                side = str(order.get("side", "")).upper()
                if side == OrderSide.BUY.value:
                    filled_buys.append((fill_price, quantity, "BROKER_BUY"))
                elif side == OrderSide.SELL.value:
                    if state.mode == StrategyMode.REVERSE:
                        tag = "REVERSE_SELL"
                    elif order.get("timeInForce") == "CLS":
                        tag = "QUARTER_SELL_LOC"
                    else:
                        tag = "TARGET_SELL_LIMIT"
                    filled_sells.append((fill_price, quantity, tag))
                else:
                    raise RuntimeError(f"Unexpected Toss order side in fill history: {side!r}")

            if filled_buys or filled_sells:
                execution_result = InfiniteStrategyAgent(state).apply_execution(filled_buys, filled_sells)
                overnight_bought_shares = execution_result["bought_shares"]
                overnight_sold_shares = execution_result["sold_shares"]
                overnight_bought_cost = round(sum(price * qty for price, qty, _ in filled_buys), 2)
                overnight_sold_revenue = round(sum(price * qty for price, qty, _ in filled_sells), 2)
                realized_pnl = execution_result["realized_pnl"]
                console.print(
                    f"  ✔ [green]전일 체결 내역 반영:[/green] "
                    f"매수 {overnight_bought_shares}주 (${overnight_bought_cost:.2f}), "
                    f"매도 {overnight_sold_shares}주 (${overnight_sold_revenue:.2f}), "
                    f"T={state.T:.2f}"
                )
            else:
                console.print("  ✔ [dim]전일 체결된 SOXL 주문 없음[/dim]")

        holdings_resp = client.get_holdings()
        items = holdings_resp.get("items", [])
        soxl_item = next((it for it in items if it.get("symbol") == settings.target_ticker), None)
        current_broker_shares = int(soxl_item.get("quantity", 0)) if soxl_item else 0
        current_broker_avg = float(soxl_item.get("averagePrice", 0.0)) if soxl_item else 0.0

        bp_resp = client.get_buying_power(currency="USD")
        raw_cash = bp_resp.get("cashBuyingPower", bp_resp.get("usdCash"))
        if raw_cash is None:
            raise RuntimeError("Toss buying-power response has no USD cash field.")
        broker_cash_usd = float(raw_cash)

        if current_broker_shares > 0:
            state.total_shares = current_broker_shares
            state.avg_price = current_broker_avg
        elif state.total_shares == 0:
            state.avg_price = 0.0
        cash_basis_cycle = _sync_strategy_cash(state, broker_cash_usd, cash_basis_cycle)
        console.print(
            f"  ✔ [green]계좌 동기화:[/green] {state.total_shares}주 @ ${state.avg_price:.2f} | "
            f"전략 계산 현금 ${state.remaining_cash:.2f} | "
            f"토스 USD 매수 가능 금액 ${broker_cash_usd:.2f} | T={state.T:.2f}"
        )
    else:
        console.print("  [dim]API 미설정으로 로컬 장부 상태를 기준으로 진행합니다.[/dim]")

    # 3. Live Price & Market Intelligence
    console.print("\n[bold yellow]▶ [2/4] 마감 종가 및 시장 인텔리전스 수집...[/bold yellow]")
    current_close = 0.0
    try:
        df = yf.download(settings.target_ticker, period="10d", interval="1d", progress=False)
        recent_5d = extract_daily_closes(df)
    except Exception as e:
        raise RuntimeError("Unable to retrieve a reliable completed SOXL daily close.") from e

    prev_close = recent_5d[-1]
    current_close = prev_close
    if client.is_configured:
        try:
            quote = client.get_current_price(settings.target_ticker)
            if quote > 0:
                current_close = quote
        except Exception as e:
            console.print(f"  [yellow]실시간 시세 조회 실패; 전일 종가로 평가합니다:[/yellow] {e}")

    macro_agent = MacroIntelligenceAgent()
    macro_res = macro_agent.fetch_and_analyze()

    semi_agent = SemiIndustryAgent()
    semi_res = semi_agent.analyze()

    fund_agent = ConstituentsFundamentalAgent()
    fund_res = fund_agent.analyze()

    console.print(
        f"  ✔ {settings.target_ticker} 전일 종가: ${prev_close:.2f} | "
        f"현재 조회가: ${current_close:.2f}"
    )
    console.print(f"  ✔ 거시 매크로: {macro_res.overall_regime} | 반도체 사이클: {semi_res.trend_state}")

    # 4. Generate Today's To-Do Orders (Agent 2)
    console.print(f"\n[bold yellow]▶ [3/4] 오늘의 무한매수 V4.0 To-Do 주문표 산출 (Agent 2)...[/bold yellow]")
    strat_agent = InfiniteStrategyAgent(state)
    order_plan = strat_agent.generate_daily_orders(
        prev_close=prev_close,
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
    exec_agent = TossExecutionAgent(
        dry_run=not live_order,
        max_live_buy_notional_usd=max_live_buy_notional_usd,
    )
    mode_tag = "실거래 주문 접수" if live_order else "모의 접수 (Dry-Run)"
    console.print(f"\n[bold yellow]▶ [4/4] 토스증권 {mode_tag} 진행 (Agent 7)...[/bold yellow]")
    exec_result = exec_agent.execute_order_plan(order_plan)
    console.print(f"  ✔ [bold]{exec_result.summary_message}[/bold]")
    if live_order and exec_result.failed_orders_count:
        raise RuntimeError(
            f"{exec_result.failed_orders_count} live order submission(s) failed or had an unknown result. "
            "Inspect Toss order history before retrying."
        )

    # 7. Journal & Markdown Report Creation (Agent 1)
    total_equity = (broker_cash_usd if broker_cash_usd is not None else state.remaining_cash) + (
        state.total_shares * current_close
    )
    unrealized_pnl = (current_close - state.avg_price) * state.total_shares if state.avg_price > 0 else 0.0
    unrealized_pct = (unrealized_pnl / (state.avg_price * state.total_shares)) * 100.0 if state.total_shares > 0 else 0.0

    record = DailyExecutionRecord(
        date=today_str,
        cycle=state.cycle,
        ticker=state.ticker,
        prev_close=prev_close,
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
        cash_basis_cycle=cash_basis_cycle,
        broker_cash_usd=broker_cash_usd,
    )
    if cash_basis_cycle is not None and broker_cash_usd is not None:
        record.notes.append(
            f"회차 #{cash_basis_cycle} 전략 계산용 현금은 예산 ${state.initial_budget:.2f}에서 "
            f"SOXL 보유원가 ${state.total_invested:.2f}를 뺀 값이며, "
            f"토스 USD 매수 가능 금액은 ${broker_cash_usd:.2f}입니다."
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
    parser.add_argument(
        "--max-live-buy-notional-usd",
        type=float,
        help="Required upper bound for total planned live SOXL buy notional in USD.",
    )
    args = parser.parse_args()
    if args.live and (
        args.max_live_buy_notional_usd is None
        or not math.isfinite(args.max_live_buy_notional_usd)
        or args.max_live_buy_notional_usd <= 0
    ):
        parser.error("--live requires a positive --max-live-buy-notional-usd.")
    run_morning_routine(
        live_order=args.live,
        max_live_buy_notional_usd=args.max_live_buy_notional_usd,
    )


if __name__ == "__main__":
    main()
