"""Test Telegram Bot Notification.

Verifies Telegram Bot Token and Chat ID by sending a test message.
"""

import sys
from rich.console import Console
from config.settings import settings
from services.telegram_notifier import TelegramNotifier

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(force_terminal=True)


def test_telegram():
    console.print("\n[bold cyan]📨 텔레그램 봇 연결 및 메시지 전송 테스트...[/bold cyan]\n")
    
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        console.print("[red]❌ .env 파일에 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID가 설정되어 있지 않습니다.[/red]")
        console.print("\n[bold yellow]📖 텔레그램 봇 발급 및 설정 방법:[/bold yellow]")
        console.print("1. 텔레그램 앱에서 [bold cyan]@BotFather[/bold cyan] 검색 후 대화 시작")
        console.print("2. [bold]/newbot[/bold] 입력 후 봇 이름 및 아이디 생성 -> [bold green]API Token[/bold green] 복사")
        console.print("3. 생성한 내 봇을 검색하여 [bold]/start[/bold] 누르기 (필수!)")
        console.print("4. 내 Chat ID 확인: 텔레그램에서 [bold cyan]@userinfobot[/bold cyan] 검색 후 [bold]/start[/bold] -> Id 숫자 복사")
        console.print("5. .env 파일에 입력:\n   [bold]TELEGRAM_BOT_TOKEN=발급받은_토큰[/bold]\n   [bold]TELEGRAM_CHAT_ID=내_Chat_ID_숫자[/bold]\n")
        return

    console.print(f"• Bot Token: {token[:6]}***{token[-4:]}")
    console.print(f"• Chat ID: {chat_id}")

    notifier = TelegramNotifier()
    test_msg = (
        "🚀 <b>[Auto Stock] 텔레그램 알림 연동 성공!</b>\n\n"
        "SOXL 무한매수법 V4.0 멀티 에이전트 시스템과 성공적으로 연결되었습니다.\n"
        "내일 아침 09:00 AM부터 어제까지의 체결 내역과 오늘의 To-Do 매매 리포트가 이 채팅방으로 자동 전송됩니다. 📈"
    )

    ok = notifier.send_message(test_msg)
    if ok:
        console.print("\n[bold green]🎉 텔레그램 테스트 메시지가 성공적으로 전송되었습니다! 텔레그램을 확인해 보세요.[/bold green]\n")
    else:
        console.print("\n[red]❌ 텔레그램 메시지 전송에 실패했습니다. Bot Token과 Chat ID를 다시 확인해 주세요.[/red]\n")


if __name__ == "__main__":
    test_telegram()

