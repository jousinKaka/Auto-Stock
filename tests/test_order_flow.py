import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest

from agents.agent2_strategy import (
    InfiniteStrategyAgent,
    OrderLine,
    OrderPlan,
    OrderSide,
    OrderType,
    StrategyState,
)
from agents.agent7_execution import TossExecutionAgent
from morning_routine import (
    _buy_only_plan,
    _calculate_unrealized_metrics,
    _closed_order_fill,
    _legacy_backfill_start_date,
    _legacy_settled_order_ids,
    _resolve_average_price,
    _strategy_shares_after_record,
    _sync_strategy_cash,
    _sync_strategy_holdings,
)
from scripts.run_routine import routine_arguments
from services.market_data import extract_daily_closes
from services.toss_client import TossSecuritiesClient, TossToken


def test_daily_close_extractor_returns_latest_completed_close():
    closes = extract_daily_closes(
        pd.DataFrame(
            {"Close": [91.0, 92.0, float("nan"), 93.67]},
            index=pd.date_range("2026-10-12", periods=4),
        )
    )

    assert closes == [91.0, 92.0, 93.67]


def test_daily_close_extractor_ignores_current_bar_before_us_market_close():
    today_ny = datetime(2026, 10, 19, 15, 59, tzinfo=ZoneInfo("America/New_York"))
    closes = extract_daily_closes(
        pd.DataFrame(
            {"Close": [93.67, 100.91]},
            index=pd.to_datetime(["2026-10-16", "2026-10-19"]),
        ),
        now=today_ny,
    )

    assert closes == [93.67]


def test_daily_close_extractor_includes_current_bar_after_us_market_close():
    market_close = datetime(2026, 10, 19, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    closes = extract_daily_closes(
        pd.DataFrame(
            {"Close": [93.67, 98.65]},
            index=pd.to_datetime(["2026-10-16", "2026-10-19"]),
        ),
        now=market_close,
    )

    assert closes == [93.67, 98.65]


def test_daily_close_extractor_converts_aware_index_to_new_york_date():
    market_close_utc = datetime(2026, 10, 19, 20, 0, tzinfo=ZoneInfo("UTC"))
    closes = extract_daily_closes(
        pd.DataFrame(
            {"Close": [93.67, 98.65]},
            index=pd.to_datetime(["2026-10-16 20:00Z", "2026-10-19 20:00Z"]),
        ),
        now=market_close_utc,
    )

    assert closes == [93.67, 98.65]


def test_unrealized_metrics_handle_zero_average_price_with_existing_shares():
    assert _calculate_unrealized_metrics(100.0, 0.0, 7) == (0.0, 0.0)


def test_invalid_broker_average_price_uses_valid_ledger_average():
    assert _resolve_average_price(0.0, 120.0) == (120.0, True)


def test_invalid_broker_and_ledger_average_prices_fail_explicitly():
    with pytest.raises(RuntimeError, match="neither the broker nor local ledger"):
        _resolve_average_price(0.0, 0.0)


def test_live_account_sync_uses_toss_average_purchase_price():
    class AccountSyncClient(FakeExecutionClient):
        def get_exchange_rate(self):
            return 1400.0

        def get_buying_power(self, currency="USD"):
            return {"cashBuyingPower": "1000.00"}

        def get_holdings(self):
            return {
                "items": [
                    {
                        "symbol": "SOXL",
                        "quantity": 109,
                        "averagePurchasePrice": "121.034403",
                    }
                ]
            }

    client = AccountSyncClient()
    agent = TossExecutionAgent(client=client, dry_run=False)
    state = StrategyState(total_shares=95, avg_price=124.5713, remaining_cash=500.0)

    synced_state, sync_info = agent.sync_account_state(state)

    assert sync_info["is_live_synced"] is True
    assert sync_info["broker_soxl_shares"] == 109
    assert sync_info["broker_soxl_avg_price"] == pytest.approx(121.034403)
    assert synced_state.total_shares == 109
    assert synced_state.avg_price == pytest.approx(121.034403)


def test_active_cycle_recovers_shares_from_starting_position_and_settled_fills():
    record = {
        "cycle": 1,
        "cash_basis_cycle": 1,
        "total_shares_before": 95,
        "total_shares_after": 7,
        "bought_shares": 7,
        "sold_shares": 0,
    }

    assert _strategy_shares_after_record(record) == 102


def test_non_active_cycle_uses_recorded_closing_shares():
    record = {
        "cycle": 2,
        "cash_basis_cycle": 1,
        "total_shares_before": 95,
        "total_shares_after": 7,
        "bought_shares": 7,
        "sold_shares": 0,
    }

    assert _strategy_shares_after_record(record) == 7


def test_legacy_settlement_matches_already_recorded_filled_order():
    order = {
        "orderId": "filled-buy-1",
        "side": "BUY",
        "execution": {"filledQuantity": "7", "filledAmount": "690.55"},
    }
    record = {"bought_shares": 7, "bought_amount": 690.55}

    assert _closed_order_fill(order) == ("filled-buy-1", "BUY", 7, 98.65, 690.55)
    assert _legacy_settled_order_ids([order], record) == {"filled-buy-1"}


def test_legacy_settlement_matches_recorded_fill_after_unrelated_closed_order():
    unrelated_order = {
        "orderId": "unrelated-buy",
        "side": "BUY",
        "execution": {"filledQuantity": "1", "filledAmount": "50.00"},
    }
    recorded_order = {
        "orderId": "recorded-buy",
        "side": "BUY",
        "execution": {"filledQuantity": "7", "filledAmount": "690.55"},
    }
    record = {"bought_shares": 7, "bought_amount": 690.55}

    assert _legacy_settled_order_ids([unrelated_order, recorded_order], record) == {
        "recorded-buy"
    }


def test_legacy_settlement_fails_when_multiple_order_subsets_match():
    orders = [
        {
            "orderId": order_id,
            "side": "BUY",
            "execution": {"filledQuantity": "7", "filledAmount": "690.55"},
        }
        for order_id in ("buy-1", "buy-2")
    ]
    record = {"bought_shares": 7, "bought_amount": 690.55}

    with pytest.raises(RuntimeError, match="Cannot uniquely match previously recorded fills"):
        _legacy_settled_order_ids(orders, record)


def test_legacy_settlement_fails_if_recorded_fills_cannot_be_matched():
    record = {"bought_shares": 7, "bought_amount": 690.55}

    with pytest.raises(RuntimeError, match="Cannot uniquely match previously recorded fills"):
        _legacy_settled_order_ids([], record)


def test_legacy_backfill_uses_previous_us_order_date():
    assert _legacy_backfill_start_date("2026-10-20") == "2026-10-19"


def test_legacy_backfill_skips_weekend_to_previous_weekday():
    assert _legacy_backfill_start_date("2026-10-19") == "2026-10-16"


def test_active_strategy_keeps_ledger_shares_when_broker_has_partial_transfer():
    state = StrategyState(cycle=1, total_shares=102, avg_price=122.79)

    used_broker_holdings = _sync_strategy_holdings(
        state,
        broker_shares=7,
        broker_average_price=0.0,
        strategy_shares_cycle=1,
    )

    assert used_broker_holdings is False
    assert state.total_shares == 102
    assert state.avg_price == 122.79


def test_strategy_uses_broker_holdings_after_special_cycle_ends():
    state = StrategyState(cycle=2, total_shares=102, avg_price=122.79)

    used_broker_holdings = _sync_strategy_holdings(
        state,
        broker_shares=7,
        broker_average_price=98.65,
        strategy_shares_cycle=1,
    )

    assert used_broker_holdings is True
    assert state.total_shares == 7
    assert state.avg_price == 98.65


def test_settling_seven_shares_restores_requested_102_share_position():
    state = StrategyState(
        cycle=1,
        T=15.78,
        total_shares=95,
        avg_price=124.5713,
        remaining_cash=18165.73,
    )

    result = InfiniteStrategyAgent(state).apply_execution(
        [(98.65, 7, "BROKER_BUY")],
        [],
    )

    assert result["new_total_shares"] == 102
    assert result["new_avg_price"] == 122.79
    assert result["new_T"] == 16.7


def test_order_plan_matches_website_inputs_for_t_value_17():
    state = StrategyState(
        T=17,
        avg_price=122.7924,
        total_shares=102,
        remaining_cash=17309.45,
        initial_budget=30000.0,
    )

    plan = InfiniteStrategyAgent(state).generate_daily_orders(
        prev_close=98.65,
        date_str="2026-10-20",
    )

    assert plan.one_unit_cash == 752.58
    assert plan.star_price == 126.48
    assert plan.target_sell_price == 147.35
    assert [
        (order.side.value, order.order_type.value, order.price, order.quantity)
        for order in plan.orders
    ] == [
        ("SELL", "LOC", 126.48, 25),
        ("SELL", "LIMIT", 147.35, 77),
        ("BUY", "LOC", 110.49, 6),
        ("BUY", "LOC", 107.51, 1),
        ("BUY", "LOC", 94.07, 1),
        ("BUY", "LOC", 83.62, 1),
        ("BUY", "LOC", 75.25, 1),
        ("BUY", "LOC", 68.41, 1),
        ("BUY", "LOC", 62.71, 1),
        ("BUY", "LOC", 57.89, 1),
    ]


def test_scheduled_morning_routine_always_uses_dry_run():
    assert routine_arguments("morning", "true", "1000") == []


def test_scheduled_evening_routine_defaults_to_dry_run():
    assert routine_arguments("evening", "false", "1000") == []
    assert routine_arguments("evening", "true", "") == []


def test_scheduled_evening_live_run_requires_explicit_enable_and_limit():
    assert routine_arguments("evening", "true", "1000") == [
        "--live",
        "--max-live-buy-notional-usd",
        "1000",
    ]


def test_scheduled_routine_rejects_unknown_slot():
    with pytest.raises(ValueError, match="Routine must be"):
        routine_arguments("noon", "false", "")


def test_toss_client_serializes_loc_payload_to_official_schema():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"result": {"orderId": "test-order"}})

    client = TossSecuritiesClient(client_id="id", client_secret="secret", account_seq="1")
    client._token = TossToken(access_token="test-token", expires_in=3600, issued_at=time.time())
    client.http_client.close()
    client.http_client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        client.create_order(
            symbol="SOXL",
            side="BUY",
            order_type="LIMIT",
            quantity=1,
            price=104.91,
            client_order_id="test-loc-order",
            time_in_force="CLS",
        )
    finally:
        client.http_client.close()

    assert captured["path"] == "/api/v1/orders"
    assert captured["body"] == {
        "symbol": "SOXL",
        "side": "BUY",
        "orderType": "LIMIT",
        "quantity": "1",
        "clientOrderId": "test-loc-order",
        "price": "104.91",
        "timeInForce": "CLS",
    }


def test_toss_client_order_history_includes_required_status_and_date_filters():
    captured = {}

    def handler(request):
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={"result": {"orders": [], "nextCursor": None, "hasNext": False}},
        )

    client = TossSecuritiesClient(client_id="id", client_secret="secret", account_seq="1")
    client._token = TossToken(access_token="test-token", expires_in=3600, issued_at=time.time())
    client.http_client.close()
    client.http_client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = client.get_order_history(
            status="CLOSED",
            symbol="SOXL",
            from_date="2026-10-19",
            to_date="2026-10-20",
            limit=100,
        )
    finally:
        client.http_client.close()

    assert result["orders"] == []
    assert captured["params"] == {
        "status": "CLOSED",
        "limit": "100",
        "symbol": "SOXL",
        "from": "2026-10-19",
        "to": "2026-10-20",
    }


def _sample_order_plan():
    return OrderPlan(
        date="2026-10-19",
        ticker="SOXL",
        state_snapshot=StrategyState(),
        prev_close=93.67,
        one_unit_cash=750.03,
        star_pct=0.04,
        star_price=129.83,
        target_sell_price=149.49,
        orders=[
            OrderLine(
                side=OrderSide.BUY,
                order_type=OrderType.LOC,
                price=104.91,
                quantity=6,
                tag="MERGED_LARGE_NUMBER_BUY",
            ),
            OrderLine(
                side=OrderSide.BUY,
                order_type=OrderType.LOC,
                price=93.75,
                quantity=1,
                tag="LADDER_BUY_1",
            ),
            OrderLine(
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=149.49,
                quantity=72,
                tag="TARGET_SELL_LIMIT",
            ),
        ],
    )


def test_buy_only_plan_excludes_sells_without_mutating_generated_plan():
    plan = _sample_order_plan()

    buy_only = _buy_only_plan(plan)

    assert len(buy_only.orders) == 2
    assert all(order.side == OrderSide.BUY for order in buy_only.orders)
    assert len(plan.orders) == 3
    assert any(order.side == OrderSide.SELL for order in plan.orders)


def test_buy_only_live_plan_does_not_require_sell_holdings(tmp_path):
    class EmptyHoldingsExecutionClient(FakeExecutionClient):
        def get_holdings(self):
            return {"items": []}

    client = EmptyHoldingsExecutionClient()
    agent = TossExecutionAgent(
        client=client,
        dry_run=False,
        max_live_buy_notional_usd=1000,
        state_dir=tmp_path,
    )
    try:
        result = agent.execute_order_plan(_buy_only_plan(_sample_order_plan()))
    finally:
        client.http_client.close()

    assert result.successful_orders_count == 2
    assert [order["side"] for order in client.orders] == ["BUY", "BUY"]


def test_active_strategy_cash_uses_toss_buying_power():
    state = StrategyState(
        cycle=1,
        T=15.78,
        total_shares=95,
        avg_price=124.5713,
        remaining_cash=18180.0,
        initial_budget=30000.0,
    )

    _sync_strategy_cash(state, broker_cash=17309.45)

    assert state.remaining_cash == 17309.45


def test_strategy_cash_rejects_invalid_toss_buying_power():
    state = StrategyState(cycle=1, remaining_cash=17000.0)

    with pytest.raises(RuntimeError, match="Toss USD buying power is invalid"):
        _sync_strategy_cash(state, broker_cash=-1.0)


class FakeExecutionClient(TossSecuritiesClient):
    def __init__(self):
        super().__init__(client_id="id", client_secret="secret", account_seq="1")
        self.orders = []
        self.open_orders = []
        self.closed_orders = []

    def get_order_history(self, status, **kwargs):
        orders = self.open_orders if status == "OPEN" else self.closed_orders
        return {"orders": orders, "hasNext": False}

    def get_buying_power(self, currency="USD"):
        return {"cashBuyingPower": "10000.00"}

    def get_holdings(self):
        return {"items": [{"symbol": "SOXL", "quantity": 100}]}

    def create_order(
        self,
        symbol,
        side,
        order_type,
        quantity,
        price=None,
        client_order_id=None,
        time_in_force=None,
    ):
        kwargs = {
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "price": price,
            "client_order_id": client_order_id,
            "time_in_force": time_in_force,
        }
        self.orders.append(kwargs)
        return {"orderId": f"order-{len(self.orders)}"}


def test_execution_agent_sends_loc_with_cls_and_stable_client_ids(tmp_path):
    client = FakeExecutionClient()
    agent = TossExecutionAgent(
        client=client,
        dry_run=False,
        max_live_buy_notional_usd=1000,
        state_dir=tmp_path,
    )
    plan = _sample_order_plan()

    result = agent.execute_order_plan(plan)
    client.http_client.close()

    assert result.successful_orders_count == 3
    assert [order["time_in_force"] for order in client.orders] == ["CLS", "CLS", "DAY"]
    ids = [order["client_order_id"] for order in client.orders]
    assert len(set(ids)) == len(ids)
    assert all(len(order_id) <= 36 for order_id in ids)
    assert ids == ["as-20261019-00", "as-20261019-01", "as-20261019-02"]


def test_live_execution_requires_buy_limit_before_any_submission(tmp_path):
    client = FakeExecutionClient()
    agent = TossExecutionAgent(
        client=client,
        dry_run=False,
        max_live_buy_notional_usd=700,
        state_dir=tmp_path,
    )
    try:
        with pytest.raises(ValueError, match="exceeding the"):
            agent.execute_order_plan(_sample_order_plan())
    finally:
        client.http_client.close()

    assert client.orders == []


def test_live_execution_blocks_existing_orders_and_replay(tmp_path):
    client = FakeExecutionClient()
    client.open_orders = [{"orderId": "existing-open-order"}]
    agent = TossExecutionAgent(
        client=client,
        dry_run=False,
        max_live_buy_notional_usd=1000,
        state_dir=tmp_path,
    )
    try:
        with pytest.raises(RuntimeError, match="existing open SOXL orders"):
            agent.execute_order_plan(_sample_order_plan())
        client.open_orders = []
        result = agent.execute_order_plan(_sample_order_plan())
    finally:
        client.http_client.close()

    assert result.successful_orders_count == 3
    assert len(client.orders) == 3


def test_live_preflight_holdings_failure_does_not_leave_execution_lock(tmp_path):
    class InsufficientHoldingsClient(FakeExecutionClient):
        def __init__(self):
            super().__init__()
            self.available_shares = 0

        def get_holdings(self):
            return {"items": [{"symbol": "SOXL", "quantity": self.available_shares}]}

    client = InsufficientHoldingsClient()
    agent = TossExecutionAgent(
        client=client,
        dry_run=False,
        max_live_buy_notional_usd=1000,
        state_dir=tmp_path,
    )
    try:
        with pytest.raises(RuntimeError, match="exceeding current holdings"):
            agent.execute_order_plan(_sample_order_plan())
        client.available_shares = 100
        result = agent.execute_order_plan(_sample_order_plan())
    finally:
        client.http_client.close()

    assert result.successful_orders_count == 3
    assert len(client.orders) == 3


def test_live_execution_allows_canceled_unfilled_closed_buy_orders(tmp_path):
    client = FakeExecutionClient()
    client.closed_orders = [
        {
            "orderId": "canceled-order",
            "side": "BUY",
            "status": "CANCELED",
            "execution": {"filledQuantity": 0},
        }
    ]
    agent = TossExecutionAgent(
        client=client,
        dry_run=False,
        max_live_buy_notional_usd=1000,
        state_dir=tmp_path,
    )
    try:
        result = agent.execute_order_plan(_sample_order_plan())
    finally:
        client.http_client.close()

    assert result.successful_orders_count == 3
    assert len(client.orders) == 3


def test_live_execution_blocks_same_day_filled_buy_order(tmp_path):
    client = FakeExecutionClient()
    client.closed_orders = [
        {
            "orderId": "filled-order",
            "side": "BUY",
            "status": "CLOSED",
            "execution": {"filledQuantity": "1"},
        }
    ]
    agent = TossExecutionAgent(
        client=client,
        dry_run=False,
        max_live_buy_notional_usd=1000,
        state_dir=tmp_path,
    )
    try:
        with pytest.raises(RuntimeError, match="same-day filled SOXL buy order"):
            agent.execute_order_plan(_sample_order_plan())
    finally:
        client.http_client.close()

    assert client.orders == []


def test_live_execution_ignores_closed_sell_fills(tmp_path):
    client = FakeExecutionClient()
    client.closed_orders = [
        {
            "orderId": "filled-sell-order",
            "side": "SELL",
            "status": "CLOSED",
            "execution": {"filledQuantity": "1"},
        }
    ]
    agent = TossExecutionAgent(
        client=client,
        dry_run=False,
        max_live_buy_notional_usd=1000,
        state_dir=tmp_path,
    )
    try:
        result = agent.execute_order_plan(_sample_order_plan())
    finally:
        client.http_client.close()

    assert result.successful_orders_count == 3


def test_live_execution_fails_closed_when_closed_buy_fill_is_unknown(tmp_path):
    client = FakeExecutionClient()
    client.closed_orders = [{"orderId": "unknown-buy", "side": "BUY"}]
    agent = TossExecutionAgent(
        client=client,
        dry_run=False,
        max_live_buy_notional_usd=1000,
        state_dir=tmp_path,
    )
    try:
        with pytest.raises(RuntimeError, match="no filled quantity"):
            agent.execute_order_plan(_sample_order_plan())
    finally:
        client.http_client.close()

    assert client.orders == []


def test_live_execution_stops_after_first_submission_failure(tmp_path):
    class FailingExecutionClient(FakeExecutionClient):
        def create_order(self, *args, **kwargs):
            self.orders.append(kwargs)
            raise TimeoutError("submission result is unknown")

    client = FailingExecutionClient()
    agent = TossExecutionAgent(
        client=client,
        dry_run=False,
        max_live_buy_notional_usd=1000,
        state_dir=tmp_path,
    )
    try:
        result = agent.execute_order_plan(_sample_order_plan())
    finally:
        client.http_client.close()

    assert result.failed_orders_count == 1
    assert len(result.orders_detail) == 1
    assert len(client.orders) == 1
    with pytest.raises(RuntimeError, match="already attempted"):
        agent.execute_order_plan(_sample_order_plan())


def test_live_execution_refuses_unconfigured_toss_client():
    class UnconfiguredClient(TossSecuritiesClient):
        @property
        def is_configured(self):
            return False

    client = UnconfiguredClient(client_id="id", client_secret="secret", account_seq="1")
    try:
        with pytest.raises(RuntimeError, match="requires configured Toss API credentials"):
            TossExecutionAgent(client=client, dry_run=False, max_live_buy_notional_usd=1000)
    finally:
        client.http_client.close()
