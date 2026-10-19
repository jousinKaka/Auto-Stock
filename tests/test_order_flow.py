import json
import time
from datetime import datetime, timedelta
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
from morning_routine import _sync_strategy_cash
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


def test_daily_close_extractor_ignores_current_new_york_date():
    today_ny = datetime.now(ZoneInfo("America/New_York")).date()
    closes = extract_daily_closes(
        pd.DataFrame(
            {"Close": [93.67, 100.91]},
            index=pd.to_datetime([today_ny - timedelta(days=1), today_ny]),
        )
    )

    assert closes == [93.67]


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


def test_strategy_cash_uses_budget_less_cost_basis_for_selected_cycle():
    state = StrategyState(
        cycle=1,
        T=15.78,
        total_shares=95,
        avg_price=124.5713,
        remaining_cash=18180.0,
        initial_budget=30000.0,
    )

    active_cycle = _sync_strategy_cash(state, broker_cash=17686.86, cash_basis_cycle=1)

    assert active_cycle == 1
    assert state.remaining_cash == 18165.73
    plan = InfiniteStrategyAgent(state).generate_daily_orders(prev_close=93.67)
    main_buy = next(order for order in plan.orders if order.tag == "MERGED_LARGE_NUMBER_BUY")
    ladder_prices = [
        order.price for order in plan.orders if order.tag.startswith("LADDER_BUY_")
    ]
    assert (main_buy.price, main_buy.quantity) == (104.91, 7)
    assert ladder_prices == [93.75, 83.33, 75.00, 68.18, 62.50, 57.69, 53.57]
    assert sum(
        order.estimated_total for order in plan.orders if order.side == OrderSide.BUY
    ) == 1228.39


def test_strategy_cash_returns_to_broker_balance_after_cycle_change():
    state = StrategyState(cycle=2, remaining_cash=17000.0)

    active_cycle = _sync_strategy_cash(state, broker_cash=17686.86, cash_basis_cycle=1)

    assert active_cycle is None
    assert state.remaining_cash == 17686.86


def test_strategy_cash_model_rejects_cost_basis_over_budget():
    state = StrategyState(
        cycle=1,
        total_shares=100,
        avg_price=310.0,
        remaining_cash=1000.0,
        initial_budget=30000.0,
    )

    with pytest.raises(RuntimeError, match="Cost-basis cash model is negative"):
        _sync_strategy_cash(state, broker_cash=1000.0, cash_basis_cycle=1)


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
        with pytest.raises(RuntimeError, match="already attempted"):
            agent.execute_order_plan(_sample_order_plan())
    finally:
        client.http_client.close()

    assert client.orders == []


def test_live_execution_blocks_closed_same_day_orders(tmp_path):
    client = FakeExecutionClient()
    client.closed_orders = [{"orderId": "existing-closed-order"}]
    agent = TossExecutionAgent(
        client=client,
        dry_run=False,
        max_live_buy_notional_usd=1000,
        state_dir=tmp_path,
    )
    try:
        with pytest.raises(RuntimeError, match="existing closed SOXL orders for 2026-10-19"):
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
