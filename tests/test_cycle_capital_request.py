import json
from types import SimpleNamespace

import pytest

from agents.agent2_strategy import StrategyState
from morning_routine import _sync_strategy_cash
from services.cycle_capital import confirm_cycle_capital, parse_capital


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeTelegram:
    is_configured = True
    bot_token = "test"
    chat_id = "123"

    def __init__(self, updates=None, fail=False):
        self.posts = []
        self.messages = []
        self.updates = updates or []
        self.fail = fail
        self.http_client = SimpleNamespace(post=self.post, get=self.get)

    def post(self, url, json):
        self.posts.append(json)
        if self.fail:
            raise RuntimeError("offline")
        return Response({"ok": True, "result": {"message_id": 50}})

    def get(self, *args, **kwargs):
        return Response({"ok": True, "result": self.updates})

    def send_message(self, text):
        self.messages.append(text)
        return True


def reply(text="30000", chat=123, prompt=50, date=100):
    return {"update_id": 1, "message": {
        "chat": {"id": chat}, "reply_to_message": {"message_id": prompt},
        "date": date, "text": text, "from": {"is_bot": False},
    }}


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "inf", "32000.01", "1.234", "hello"])
def test_reject_invalid_capital(value):
    assert parse_capital(value, 32000) is None


def test_accept_exact_available_and_commas():
    assert parse_capital("32,000.00", 32000) == 32000


def test_reply_changes_budget_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("services.cycle_capital.time.time", lambda: 100)
    path = tmp_path / "requests.json"
    state = StrategyState(cycle=2, initial_budget=32000, remaining_cash=32000)
    tg = FakeTelegram([reply()])
    confirm_cycle_capital(state, path, notifier=tg)
    assert state.initial_budget == state.remaining_cash == 30000
    assert state.excluded_cash == 2000
    assert len(tg.posts) == 1
    restored = StrategyState(cycle=2, initial_budget=32000, remaining_cash=32000)
    confirm_cycle_capital(restored, path, notifier=tg)
    assert restored.initial_budget == 30000
    assert len(tg.posts) == 1
    _sync_strategy_cash(restored, 32000)
    assert restored.remaining_cash == 30000


@pytest.mark.parametrize("update", [reply(chat=456), reply(prompt=49), reply(date=999), reply("50000")])
def test_unrelated_late_or_invalid_reply_keeps_default(tmp_path, monkeypatch, update):
    ticks = iter([100, 100, 101, 102, 103, 110])
    monkeypatch.setattr("services.cycle_capital.time.time", lambda: next(ticks, 110))
    monkeypatch.setattr("services.cycle_capital.time.sleep", lambda _: None)
    state = StrategyState(cycle=2, initial_budget=32000, remaining_cash=32000)
    confirm_cycle_capital(state, tmp_path / "r.json", wait_seconds=5, notifier=FakeTelegram([update]))
    assert state.initial_budget == state.remaining_cash == 32000


def test_no_reply_prompts_once_and_keeps_default(tmp_path):
    state = StrategyState(cycle=2, initial_budget=32000, remaining_cash=32000)
    tg = FakeTelegram()
    path = tmp_path / "r.json"
    confirm_cycle_capital(state, path, wait_seconds=0, notifier=tg)
    confirm_cycle_capital(state, path, wait_seconds=0, notifier=tg)
    assert len(tg.posts) == 1
    assert state.remaining_cash == 32000
    assert json.loads(path.read_text())["SOXL:2"]["done"]


def test_network_failure_keeps_default(tmp_path):
    state = StrategyState(cycle=2, initial_budget=32000, remaining_cash=32000)
    confirm_cycle_capital(state, tmp_path / "r.json", notifier=FakeTelegram(fail=True))
    assert state.remaining_cash == 32000


@pytest.mark.parametrize("values", [{"cycle": 1}, {"cycle": 2, "total_shares": 1}, {"cycle": 2, "T": 1}])
def test_no_question_for_initial_or_in_progress_cycle(tmp_path, values):
    tg = FakeTelegram()
    confirm_cycle_capital(StrategyState(**values), tmp_path / "r.json", notifier=tg)
    assert not tg.posts
