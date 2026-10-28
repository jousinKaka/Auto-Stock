"""직접 실행하는 계좌 증감 표시 미리보기. 장부와 자동 알림은 변경하지 않습니다."""

from services.telegram_notifier import TelegramNotifier


def main():
    message = """🚀 <b>[SOXL 무한매수 V4.0] 09:00 KST 아침 루틴 (체결 정산·분석 (주문 없음))</b>
📅 <b>기준일자</b>: 2026-10-28 | <b>회차</b>: #1 | <b>모드</b>: NORMAL

<b>💼 밤사이 체결 정산 및 계좌 현황</b>
• <b>진행도 (T값)</b>: <code>22.38 / 40</code> · 후반전 <b>(+0.97)</b>
• <b>보유 주식</b>: <b>147주 (+7주)</b>
• <b>평단가</b>: <b>$113.52 (−$0.62)</b>
• <b>현재가</b>: <b>$100.65 (+$10.15 · +11.22%)</b>
• <b>평가 손익</b>: <b>−$1,891.71 · −11.34% (+$906.99 · 수익률 +6.17%p)</b>
• <b>전략 계산용 현금</b>: <b>$12,900.16 (−$708.26)</b>
• <b>토스 실제 SOXL 보유량</b>: <b>147주 (+7주)</b>
• <b>토스 USD 매수 가능 금액</b>: <b>$12,900.16 (−$708.26)</b>
• <b>총 자산 가치</b>: <b>$27,695.71 (+$906.29 · +3.38%)</b>

<b>⚡ 전날 미국장 체결 정산</b>
• 매수 체결: <b>7주 · $707.56</b>
• 실현 손익: <b>$0.00</b>"""
    notifier = TelegramNotifier()
    try:
        success = notifier.send_message(message)
        print("미리보기 발송 성공" if success else "미리보기 발송 실패")
        if not success:
            raise SystemExit(1)
    finally:
        notifier.http_client.close()


if __name__ == "__main__":
    main()
