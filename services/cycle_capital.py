"""새 회차 시작 원금을 질문하고, 무응답이면 자동 계산값을 유지합니다."""

import json
import logging
import math
import re
import time
from pathlib import Path

from services.telegram_notifier import TelegramNotifier


def parse_capital(text: str, available: float):
    value = text.strip().replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
        return None
    amount = float(value)
    return amount if math.isfinite(amount) and 0 < amount <= available else None


def confirm_cycle_capital(state, path: Path, wait_seconds=300, notifier=None):
    """최초 회차와 이미 매수한 회차는 제외하고 회차별 질문을 한 번만 보냅니다."""
    if state.cycle <= 1 or state.total_shares or state.T:
        return
    key = f"{state.ticker}:{state.cycle}"
    requests = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    request = requests.get(key)
    available = round(state.remaining_cash + state.excluded_cash, 2)

    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(requests, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def apply():
        # 재실행 시 일지 저장 전에 확정된 선택도 동일하게 복원합니다.
        if request.get("chosen") is not None:
            state.initial_budget = request["chosen"]
            state.remaining_cash = min(request["chosen"], available)
            state.excluded_cash = max(0.0, round(available - state.remaining_cash, 2))

    if request and request.get("done"):
        apply()
        return
    tg = notifier or TelegramNotifier()
    if not tg.is_configured:
        return
    api = f"https://api.telegram.org/bot{tg.bot_token}"
    try:
        if request is None:
            # 전송 전에 기록해 재시작 때 같은 질문을 중복 발송하지 않습니다.
            request = {"deadline": time.time() + wait_seconds, "done": False}
            requests[key] = request
            save()
            response = tg.http_client.post(api + "/sendMessage", json={
                "chat_id": tg.chat_id,
                "text": (
                    f"회차 #{state.cycle} 시작자산을 얼마로 할까요?\n"
                    f"자동 계산: ${state.initial_budget:,.2f}\n"
                    f"이 메시지에 답장으로 달러 금액을 입력해 주세요 (예: 30000).\n"
                    f"{wait_seconds}초 안에 답이 없으면 자동 계산 금액으로 시작합니다.\n"
                    f"입력 가능 범위: $0 초과 ~ ${available:,.2f}."
                ),
                "reply_markup": {"force_reply": True, "selective": True},
            })
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise ValueError("질문 전송 실패")
            request["message_id"] = payload["result"]["message_id"]
            save()
        if "message_id" not in request:
            request["done"] = True
            save()
            return
        while time.time() < request["deadline"]:
            response = tg.http_client.get(api + "/getUpdates", params={
                "offset": request.get("offset", 0), "timeout": 0,
                "allowed_updates": '["message"]',
            })
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise ValueError("답변 조회 실패")
            for update in payload["result"]:
                request["offset"] = update["update_id"] + 1
                message = update.get("message", {})
                if (str(message.get("chat", {}).get("id")) != str(tg.chat_id)
                    or message.get("from", {}).get("is_bot")
                    or message.get("reply_to_message", {}).get("message_id") != request["message_id"]
                    or message.get("date", 0) > request["deadline"]):
                    continue
                amount = parse_capital(message.get("text", ""), available)
                if amount is None:
                    tg.send_message("금액을 확인해 주세요. 안내 메시지에 답장으로 가용 현금 이내의 양수 금액을 입력해 주세요.")
                    continue
                request.update(chosen=amount, done=True)
                save()
                apply()
                tg.send_message(f"회차 #{state.cycle} 시작자산을 ${amount:,.2f}로 적용합니다.")
                return
            save()
            time.sleep(min(2, max(0, request["deadline"] - time.time())))
        request["done"] = True
        save()
        tg.send_message(f"응답 대기 종료: 회차 #{state.cycle}는 자동 계산된 ${state.initial_budget:,.2f}로 시작합니다.")
    except Exception:
        # 통신 실패가 주문 루틴을 중단하거나 기본값을 바꾸지 않게 합니다.
        logging.getLogger(__name__).warning("회차 시작자산 질문 통신 실패: 자동 계산 금액으로 진행합니다.")
        request["done"] = True
        save()
