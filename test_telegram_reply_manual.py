"""직접 실행하는 텔레그램 답장 테스트. 실제 장부와 주문은 변경하지 않습니다."""

import json
import tempfile
from pathlib import Path

from agents.agent2_strategy import StrategyState
from services.cycle_capital import confirm_cycle_capital
from services.telegram_notifier import TelegramNotifier


def main():
    notifier = TelegramNotifier()
    if not notifier.is_configured:
        print("텔레그램 설정이 없어 테스트할 수 없습니다.", flush=True)
        return

    class TestClient:
        def post(self, url, json):
            json = dict(json)
            json["text"] = "[답장 수신 테스트 · 실제 투자금 변경 없음]\n" + json["text"]
            response = notifier.http_client.post(url, json=json)
            print(f"테스트 질문 전송 HTTP 상태: {response.status_code}", flush=True)
            return response

        def get(self, *args, **kwargs):
            return notifier.http_client.get(*args, **kwargs)

    class TestNotifier:
        is_configured = True
        bot_token = notifier.bot_token
        chat_id = notifier.chat_id
        http_client = TestClient()

        def send_message(self, text):
            return notifier.send_message("[테스트 · 실제 투자금 변경 없음]\n" + text)

    state = StrategyState(cycle=2, initial_budget=32000, remaining_cash=32000)
    with tempfile.TemporaryDirectory(prefix="auto_stock_telegram_test_") as directory:
        path = Path(directory) / "requests.json"
        confirm_cycle_capital(state, path, wait_seconds=300, notifier=TestNotifier())
        request = json.loads(path.read_text(encoding="utf-8")).get("SOXL:2", {}) if path.exists() else {}
        if request.get("chosen") is not None:
            print(f"답장 수신 성공: 테스트 시작자산 ${state.initial_budget:,.2f}", flush=True)
        else:
            print("답장 미확인: 시간 초과 또는 통신 실패. 실제 장부 변경 없음.", flush=True)
    notifier.http_client.close()


if __name__ == "__main__":
    main()
