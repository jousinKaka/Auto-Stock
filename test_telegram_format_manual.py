"""직접 실행하는 텔레그램 카드 서식 미리보기. 모든 숫자는 예시입니다."""

from agents.agent6_fundamental import SOXLConstituentsAnalysis, ConstituentStockInfo
from services.telegram_notifier import TelegramNotifier, _analysis_messages


def main():
    report = SOXLConstituentsAnalysis(
        timestamp="서식 확인용 예시 데이터",
        companies=[
            ConstituentStockInfo(
                symbol="NVDA", company_name="엔비디아", current_price=216,
                change_1d_pct=2.39, next_earnings_date="2026-11-18",
                consensus_eps=2.47, consensus_revenue=108980000000,
                key_metrics_to_watch="데이터센터 매출 성장, 가속기 수요",
            ),
            ConstituentStockInfo(
                symbol="AVGO", company_name="브로드컴", current_price=329.17,
                change_1d_pct=2.33, next_earnings_date="2026-12-10",
                consensus_eps=3.84, consensus_revenue=34910000000,
                key_metrics_to_watch="AI 반도체 매출, 커스텀 가속기 수요",
            ),
        ],
    )
    notifier = TelegramNotifier()
    try:
        messages = _analysis_messages(
            "서식 테스트 · 실제 시세 아님",
            [("주요 기업·실적 일정 · 미리보기", report.to_markdown())],
        )
        results = [notifier.send_message(message) for message in messages]
        print("테스트 발송 성공" if all(results) else "테스트 발송 실패")
    finally:
        notifier.http_client.close()


if __name__ == "__main__":
    main()
