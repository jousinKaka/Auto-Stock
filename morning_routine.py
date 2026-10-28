"""Morning and evening routine for the daily automated stock manager.

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
import os
import sys
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import yfinance as yf
from rich.console import Console
from rich.table import Table

from agents import (
    ConstituentsFundamentalAgent,
    DailyExecutionRecord,
    ExecutionJournalAgent,
    InfiniteStrategyAgent,
    GoogleSheetsAccountSyncAgent,
    GoogleSheetsSyncError,
    MacroIntelligenceAgent,
    OrderPlan,
    OrderSide,
    RiskWatchdogAgent,
    SemiIndustryAgent,
    StrategyMode,
    StrategyState,
    TossExecutionAgent,
)
from config.settings import settings
from config.google_sheets_config import google_sheets_config
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
) -> None:
    if not math.isfinite(broker_cash) or broker_cash < 0:
        raise RuntimeError(f"Toss USD buying power is invalid: {broker_cash!r}.")
    state.remaining_cash = max(0.0, round(broker_cash - state.excluded_cash, 2))


def _strategy_shares_after_record(record: dict) -> int:
    strategy_shares_cycle = record.get(
        "strategy_shares_cycle",
        record.get("cash_basis_cycle"),
    )
    if strategy_shares_cycle == record.get("cycle"):
        shares = (
            int(record.get("total_shares_before", 0))
            + int(record.get("bought_shares", 0))
            - int(record.get("sold_shares", 0))
        )
        if shares < 0:
            raise RuntimeError("Recorded strategy holdings are negative; review the trade ledger.")
        return shares
    return int(record.get("total_shares_after", 0))


def _closed_order_fill(order: dict) -> Optional[tuple[str, str, int, float, float]]:
    execution = order.get("execution") or {}
    filled_value = float(execution.get("filledQuantity") or 0)
    if not math.isfinite(filled_value) or filled_value < 0:
        raise ValueError(f"Toss order has an invalid filled quantity: {filled_value}.")
    if filled_value == 0:
        return None

    quantity = int(filled_value)
    if quantity != filled_value:
        raise ValueError(
            f"Fractional SOXL fills are not supported by the strategy ledger: {filled_value} shares."
        )

    side = str(order.get("side", "")).upper()
    if side not in {OrderSide.BUY.value, OrderSide.SELL.value}:
        raise RuntimeError(f"Unexpected Toss order side in fill history: {side!r}")

    execution_amount = execution.get("filledAmount")
    average_price = execution.get("averageFilledPrice")
    fill_amount = float(execution_amount) if execution_amount is not None else None
    if average_price is not None:
        fill_price = float(average_price)
    elif fill_amount is not None:
        fill_price = round(fill_amount / quantity, 8)
    else:
        raise RuntimeError("Toss execution record has no fill price or filled amount.")
    if execution_amount is not None:
        if not math.isfinite(fill_amount):
            raise RuntimeError("Toss execution record has an invalid fill amount.")
    else:
        fill_amount = fill_price * quantity

    if not math.isfinite(fill_price) or fill_price <= 0 or not math.isfinite(fill_amount):
        raise RuntimeError("Toss execution record has an invalid fill price or amount.")

    order_id = order.get("orderId", order.get("id"))
    if order_id is None or not str(order_id).strip():
        raise RuntimeError("Toss filled order has no order ID; cannot safely deduplicate settlement.")
    return str(order_id), side, quantity, fill_price, fill_amount


def _sync_strategy_holdings(
    state: StrategyState,
    broker_shares: int,
    broker_average_price: float,
    strategy_shares_cycle: Optional[int],
) -> bool:
    if strategy_shares_cycle == state.cycle:
        if state.total_shares > 0 and state.avg_price <= 0:
            raise RuntimeError("Active strategy ledger has shares but no valid average price.")
        return False

    if broker_shares > 0:
        state.total_shares = broker_shares
        state.avg_price, _ = _resolve_average_price(
            broker_average_price,
            state.avg_price,
        )
        return True

    if state.total_shares == 0:
        state.avg_price = 0.0
    return False


def _legacy_settled_order_ids(closed_orders: list[dict], record: dict) -> set[str]:
    if "settled_order_ids" in record:
        return set()

    settled_ids: set[str] = set()
    for side, shares_key, amount_key in (
        (OrderSide.BUY.value, "bought_shares", "bought_amount"),
        (OrderSide.SELL.value, "sold_shares", "sold_amount"),
    ):
        target_shares = int(record.get(shares_key, 0) or 0)
        target_amount = round(float(record.get(amount_key, 0) or 0), 2)
        if target_shares <= 0 and target_amount <= 0:
            continue
        if target_shares <= 0 or target_amount <= 0:
            raise RuntimeError(
                "Legacy ledger has incomplete fill totals; inspect Toss order history before continuing."
            )

        target_cents = round(target_amount * 100)
        candidates: list[tuple[str, int, int]] = []
        for order in closed_orders:
            fill = _closed_order_fill(order)
            if fill is None or fill[1] != side:
                continue
            order_id, _, quantity, _, amount = fill
            candidates.append((order_id, quantity, round(amount * 100)))

        # Legacy records stored aggregate fills, so match any exact order subset
        # rather than assuming those fills were the first orders returned.
        matches: set[tuple[str, ...]] = set()
        reachable: dict[tuple[int, int], set[tuple[str, ...]]] = {(0, 0): {()}}
        for order_id, quantity, amount_cents in candidates:
            next_reachable = {state: set(ids) for state, ids in reachable.items()}
            for (shares, cents), id_sets in reachable.items():
                new_shares = shares + quantity
                new_cents = cents + amount_cents
                if new_shares > target_shares or new_cents > target_cents + 2:
                    continue
                key = (new_shares, new_cents)
                known = next_reachable.setdefault(key, set())
                for ids in id_sets:
                    if len(known) < 2:
                        known.add((*ids, order_id))
            reachable = next_reachable

        for (shares, cents), id_sets in reachable.items():
            if shares == target_shares and abs(cents - target_cents) <= 2:
                matches.update(id_sets)
                if len(matches) > 1:
                    break

        if len(matches) != 1:
            raise RuntimeError(
                "Cannot uniquely match previously recorded fills to Toss order IDs; "
                "inspect the ledger and broker history before settling again."
            )
        settled_ids.update(next(iter(matches)))
    return settled_ids


def _legacy_backfill_start_date(record_date: str) -> str:
    previous_day = date.fromisoformat(record_date) - timedelta(days=1)
    while previous_day.weekday() >= 5:
        previous_day -= timedelta(days=1)
    return previous_day.isoformat()


def _resolve_average_price(
    broker_average_price: float,
    ledger_average_price: float,
) -> tuple[float, bool]:
    if math.isfinite(broker_average_price) and broker_average_price > 0:
        return broker_average_price, False
    if math.isfinite(ledger_average_price) and ledger_average_price > 0:
        return ledger_average_price, True
    raise RuntimeError(
        "Toss holdings contain shares but neither the broker nor local ledger "
        "has a valid average price."
    )


def _calculate_unrealized_metrics(
    current_price: float,
    average_price: float,
    total_shares: int,
) -> tuple[float, float]:
    if total_shares <= 0 or not math.isfinite(average_price) or average_price <= 0:
        return 0.0, 0.0

    unrealized_pnl = (current_price - average_price) * total_shares
    cost_basis = average_price * total_shares
    unrealized_pct = (unrealized_pnl / cost_basis) * 100.0
    return unrealized_pnl, unrealized_pct


def _buy_only_plan(order_plan: OrderPlan) -> OrderPlan:
    return order_plan.model_copy(
        update={
            "orders": [
                order for order in order_plan.orders if order.side == OrderSide.BUY
            ]
        }
    )


def run_morning_routine(
    live_order: bool = False,
    max_live_buy_notional_usd: Optional[float] = None,
    live_buy_only: bool = False,
) -> None:
    if live_buy_only and not live_order:
        raise ValueError("Buy-only mode is available only for live order execution.")

    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    today_str = now_kst.strftime("%Y-%m-%d")
    now_str = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    routine_slot = os.environ.get("AUTO_STOCK_ROUTINE")
    if routine_slot is None:
        routine_slot = "evening" if now_kst.hour >= 12 else "morning"
    if routine_slot not in {"morning", "evening"}:
        raise ValueError(f"AUTO_STOCK_ROUTINE must be 'morning' or 'evening', got {routine_slot!r}.")
    routine_time_label = "20:00 KST 저녁" if routine_slot == "evening" else "09:00 KST 아침"

    console.print(f"\n[bold cyan]🌅 [{routine_time_label} 루틴 시작] SOXL 무한매수 V4.0 자동화 ({now_str})[/bold cyan]\n")

    journal_agent = ExecutionJournalAgent()
    history = journal_agent.load_history()

    # 1. Load Last Recorded State
    if history:
        latest = history[-1]
        strategy_shares_cycle = latest.get(
            "strategy_shares_cycle",
            latest.get("cash_basis_cycle"),
        )
        if strategy_shares_cycle != latest.get("cycle"):
            strategy_shares_cycle = None
        same_day_record = latest.get("date") == today_str
        state = StrategyState(
            ticker=latest.get("ticker", settings.target_ticker),
            cycle=latest.get("cycle", 1),
            divisions=settings.divisions,
            target_profit_rate=settings.target_profit_rate,
            large_number_ratio=settings.large_number_ratio,
            T=latest.get("T_after", 0.0),
            avg_price=latest.get("avg_price_after", 0.0),
            total_shares=_strategy_shares_after_record(latest),
            remaining_cash=latest.get("remaining_cash_after", settings.total_budget_usd),
            initial_budget=latest.get("initial_budget") or settings.total_budget_usd,
            excluded_cash=latest.get("excluded_cash", 0.0),
            mode=StrategyMode(latest.get("mode", StrategyMode.NORMAL.value)),
        )
    else:
        same_day_record = False
        # Initial default or mid-entry state
        state = StrategyState(
            ticker=settings.target_ticker,
            divisions=settings.divisions,
            target_profit_rate=settings.target_profit_rate,
            large_number_ratio=settings.large_number_ratio,
            remaining_cash=settings.total_budget_usd,
            initial_budget=settings.total_budget_usd,
        )
        strategy_shares_cycle = None

    # 2. Check Overnight Executions via Toss Securities API
    console.print("[bold yellow]▶ [1/4] 전일 야간 미국장 체결 결과 확인 (토스증권 API)...[/bold yellow]")
    client = TossSecuritiesClient()
    
    overnight_bought_shares = int(latest.get("bought_shares", 0)) if same_day_record else 0
    overnight_bought_cost = float(latest.get("bought_amount", 0.0)) if same_day_record else 0.0
    overnight_sold_shares = int(latest.get("sold_shares", 0)) if same_day_record else 0
    overnight_sold_revenue = float(latest.get("sold_amount", 0.0)) if same_day_record else 0.0
    realized_pnl = float(latest.get("realized_pnl", 0.0)) if same_day_record else 0.0
    settled_order_ids = list(latest.get("settled_order_ids", [])) if same_day_record else []
    prev_shares = int(latest.get("total_shares_before", state.total_shares)) if same_day_record else state.total_shares
    prev_avg_price = float(latest.get("avg_price_before", state.avg_price)) if same_day_record else state.avg_price
    prev_T = float(latest.get("T_before", state.T)) if same_day_record else state.T
    prev_cash = float(latest.get("remaining_cash_before", state.remaining_cash)) if same_day_record else state.remaining_cash
    broker_cash_usd: Optional[float] = None
    broker_soxl_shares: Optional[int] = None

    if client.is_configured:
        # Closed orders provide actual fill quantities, prices, and LOC/limit types;
        # a holdings delta alone cannot distinguish quarter sells from target sells.
        needs_legacy_backfill = (
            history
            and same_day_record
            and "settled_order_ids" not in latest
            and (overnight_bought_shares > 0 or overnight_sold_shares > 0)
        )
        if history and (latest.get("date", today_str) < today_str or needs_legacy_backfill):
            cursor = None
            closed_orders = []
            history_start_date = (
                _legacy_backfill_start_date(latest["date"])
                if needs_legacy_backfill
                else latest["date"]
            )
            while True:
                page = client.get_order_history(
                    status="CLOSED",
                    symbol=settings.target_ticker,
                    from_date=history_start_date,
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
            already_settled_ids = set(latest.get("settled_order_ids", []))
            if "settled_order_ids" not in latest:
                already_settled_ids.update(_legacy_settled_order_ids(closed_orders, latest))
            settled_order_ids.extend(already_settled_ids)
            filled_buys = []
            filled_sells = []
            for order in closed_orders:
                fill = _closed_order_fill(order)
                if fill is None:
                    continue
                order_id, side, quantity, fill_price, _ = fill
                if order_id in already_settled_ids:
                    continue
                settled_order_ids.append(order_id)
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
                overnight_bought_shares += execution_result["bought_shares"]
                overnight_sold_shares += execution_result["sold_shares"]
                overnight_bought_cost = round(
                    overnight_bought_cost + sum(price * qty for price, qty, _ in filled_buys),
                    2,
                )
                overnight_sold_revenue = round(
                    overnight_sold_revenue + sum(price * qty for price, qty, _ in filled_sells),
                    2,
                )
                realized_pnl += execution_result["realized_pnl"]
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
        broker_soxl_shares = current_broker_shares
        current_broker_avg = (
            float(soxl_item.get("averagePurchasePrice", soxl_item.get("averagePrice", 0.0)))
            if soxl_item
            else 0.0
        )

        bp_resp = client.get_buying_power(currency="USD")
        raw_cash = bp_resp.get("cashBuyingPower", bp_resp.get("usdCash"))
        if raw_cash is None:
            raise RuntimeError("Toss buying-power response has no USD cash field.")
        broker_cash_usd = float(raw_cash)

        used_broker_holdings = _sync_strategy_holdings(
            state,
            current_broker_shares,
            current_broker_avg,
            strategy_shares_cycle,
        )
        if strategy_shares_cycle == state.cycle and current_broker_shares != state.total_shares:
            console.print(
                "  [yellow]이번 회차 전략 장부 보유량을 유지합니다: "
                f"{state.total_shares}주 (토스 실계좌 {current_broker_shares}주).[/yellow]"
            )
        elif used_broker_holdings and current_broker_avg <= 0:
            console.print(
                "  [yellow]토스 보유내역의 평균 매수가가 유효하지 않아 "
                f"장부 평단 ${state.avg_price:.2f}을 유지합니다.[/yellow]"
            )
        _sync_strategy_cash(state, broker_cash_usd)
        console.print(
            f"  ✔ [green]계좌 동기화:[/green] {state.total_shares}주 @ ${state.avg_price:.2f} | "
            f"전략 계산 현금 ${state.remaining_cash:.2f} | "
            f"토스 USD 매수 가능 금액 ${broker_cash_usd:.2f} | T={state.T:.2f}"
        )
    else:
        console.print("  [dim]API 미설정으로 로컬 장부 상태를 기준으로 진행합니다.[/dim]")

    from services.cycle_capital import confirm_cycle_capital
    confirm_cycle_capital(
        state, settings.data_dir / "cycle_capital_requests.json",
        wait_seconds=settings.cycle_capital_wait_seconds,
    )

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
    if live_buy_only:
        order_plan = _buy_only_plan(order_plan)
        console.print("  [yellow]오늘은 매수 주문만 제출하며 매도 주문은 제외합니다.[/yellow]")

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
    equity_shares = broker_soxl_shares if broker_soxl_shares is not None else state.total_shares
    total_equity = (broker_cash_usd if broker_cash_usd is not None else state.remaining_cash) + (
        equity_shares * current_close
    )
    unrealized_pnl, unrealized_pct = _calculate_unrealized_metrics(
        current_close,
        state.avg_price,
        state.total_shares,
    )

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
        initial_budget=state.initial_budget,
        excluded_cash=state.excluded_cash,
        bought_shares=overnight_bought_shares,
        bought_amount=overnight_bought_cost,
        sold_shares=overnight_sold_shares,
        sold_amount=overnight_sold_revenue,
        realized_pnl=realized_pnl,
        unrealized_pnl=round(unrealized_pnl, 2),
        unrealized_return_pct=round(unrealized_pct, 2),
        total_equity=round(total_equity, 2),
        investment_rationale=(
            f"{routine_time_label} 정기 루틴: 전일 체결 정산 후 T값 {state.T:.2f} 기준으로 "
            f"오늘의 1회 매수금 ${order_plan.one_unit_cash:.2f} 주문표 생성 및 {mode_tag} 완료."
            + (" (사용자 요청으로 매수 주문만 포함)" if live_buy_only else "")
        ),
        notes=[
            f"야간 체결: 매수 {overnight_bought_shares}주 / 매도 {overnight_sold_shares}주",
            f"매크로: {macro_res.overall_regime} | 반도체: {semi_res.trend_state}",
            f"리스크 등급: {risk_res.risk_level.value}",
        ],
        strategy_shares_cycle=strategy_shares_cycle,
        broker_cash_usd=broker_cash_usd,
        broker_soxl_shares=broker_soxl_shares,
        broker_soxl_avg_price=current_broker_avg if broker_soxl_shares is not None else None,
        settled_order_ids=sorted(set(settled_order_ids)),
    )
    if (
        broker_soxl_shares is not None
        and strategy_shares_cycle == state.cycle
        and broker_soxl_shares != state.total_shares
    ):
        record.notes.append(
            "전략 회차 장부 보유량과 토스 실계좌 보유량이 다릅니다. "
            "입고/이체가 완료되어 실제 계좌 수량이 일치하는지 확인하세요."
        )
    if strategy_shares_cycle is not None and broker_cash_usd is not None:
        record.notes.append(
            f"회차 #{strategy_shares_cycle}의 전략 보유량은 장부 기준으로 유지하며, "
            f"주문표 현금은 토스 USD 매수 가능 금액 ${broker_cash_usd:.2f}을 사용합니다."
        )
    if live_buy_only:
        record.notes.append("사용자 요청으로 오늘의 실거래 계획에서 매도 주문을 제외하고 매수만 제출합니다.")
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
    console.print(f"\n✔ [green]일일 투자 보고서 저장 완료:[/green] [link=file:///{report_path}]{report_path}[/link]")

    sheets_sync_error: Optional[GoogleSheetsSyncError] = None
    if routine_slot == "morning" and google_sheets_config.enabled:
        try:
            sync_summary = GoogleSheetsAccountSyncAgent(
                client=client,
                config=google_sheets_config,
            ).sync()
            console.print(f"  ✔ [green]Agent 8:[/green] {sync_summary}")
        except GoogleSheetsSyncError as exc:
            sheets_sync_error = exc
            console.print(f"  ✖ [bold red]Agent 8 Google Sheets 동기화 실패:[/bold red] {exc}")

    # 8. Send Telegram Briefing
    from services.telegram_notifier import TelegramNotifier, previous_account_record
    tg = TelegramNotifier()
    if tg.is_configured:
        tg_ok = tg.send_morning_briefing(
            record=record,
            order_plan=order_plan,
            routine_slot=routine_slot,
            live_order=live_order,
            live_buy_only=live_buy_only,
            execution_summary=exec_result.summary_message,
            macro_summary=macro_res.to_markdown(),
            semi_summary=semi_res.to_markdown(),
            fundamental_summary=fund_res.to_markdown(),
            previous_record=previous_account_record(history, today_str, state.ticker),
        )
        if tg_ok:
            console.print("  ✔ [green]텔레그램 일일 리포트 발송 완료![/green]")
        else:
            console.print("  ⚠️ [yellow]텔레그램 발송 실패 (토큰 및 Chat ID 확인 필요)[/yellow]")
    else:
        console.print("  [dim]💡 텔레그램 알림 미설정 (.env에 토큰과 Chat ID 설정 시 매일 아침 자동 전송)[/dim]")

    if sheets_sync_error is not None:
        if tg.is_configured:
            tg.send_message(
                f"⚠️ Agent 8 Google Sheets 계좌 현황 동기화에 실패했습니다.\n"
                f"서버 로그를 확인해 주세요: {sheets_sync_error}"
            )
        raise sheets_sync_error

    console.print(f"[bold green]🎉 {routine_time_label} 루틴이 완료되었습니다![/bold green]\n")


def main():
    parser = argparse.ArgumentParser(description="Auto Stock morning/evening routine")
    parser.add_argument("--live", action="store_true", help="Submit actual orders to Toss Securities OpenAPI")
    parser.add_argument(
        "--buy-only",
        action="store_true",
        help="In live mode, omit all planned sell orders from today's execution.",
    )
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
    if args.buy_only and not args.live:
        parser.error("--buy-only requires --live.")
    run_morning_routine(
        live_order=args.live,
        max_live_buy_notional_usd=args.max_live_buy_notional_usd,
        live_buy_only=args.buy_only,
    )


if __name__ == "__main__":
    main()
