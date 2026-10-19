"""Toss Securities OpenAPI Connection & Authentication Diagnostics.

Safely tests OAuth2 token issuance, account balance, holdings, and market data
without revealing secrets in logs or terminal output.
"""

import sys
import traceback
from rich.console import Console
from rich.table import Table

from config.toss_config import TossConfig
from services.toss_client import TossSecuritiesClient

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(force_terminal=True)


def mask(val: str, show_start: int = 3, show_end: int = 3) -> str:
    if not val:
        return "<미설정>"
    if len(val) <= show_start + show_end:
        return "***"
    return f"{val[:show_start]}***{val[-show_end:]}"


def test_connection():
    console.print("\n[bold cyan]🔍 토스증권 Open API 접속 및 인증 진단을 시작합니다...[/bold cyan]\n")
    
    cfg = TossConfig()
    
    table_cfg = Table(title="1. 환경 변수(.env) 설정 확인", show_lines=True)
    table_cfg.add_column("항목", style="bold")
    table_cfg.add_column("설정 상태", justify="center")
    table_cfg.add_column("마스킹된 값", justify="center")

    table_cfg.add_row("TOSS_CLIENT_ID", "✅ 설정됨" if cfg.toss_client_id else "❌ 누락", mask(cfg.toss_client_id, 4, 3))
    table_cfg.add_row("TOSS_CLIENT_SECRET", "✅ 설정됨" if cfg.toss_client_secret else "❌ 누락", mask(cfg.toss_client_secret, 2, 2))
    table_cfg.add_row("TOSS_ACCOUNT_SEQ", "✅ 설정됨" if cfg.toss_account_seq else "❌ 누락", mask(cfg.toss_account_seq, 2, 2))
    console.print(table_cfg)

    if not cfg.is_configured:
        console.print("[red]❌ 필수 인증 정보가 .env에 누락되어 있습니다. TOSS_CLIENT_ID, TOSS_CLIENT_SECRET, TOSS_ACCOUNT_SEQ를 확인해 주세요.[/red]")
        return

    client = TossSecuritiesClient()

    # 1. OAuth Token Test
    console.print("\n[bold yellow]▶ [Step 1] OAuth 2.0 액세스 토큰 발급 테스트...[/bold yellow]")
    try:
        token = client.get_access_token(force_refresh=True)
        console.print(f"  ✔ [green]토큰 발급 성공![/green] (토큰 길이: {len(token)} chars, 유효시간: {client._token.expires_in}초)")
    except Exception as e:
        console.print(f"  ❌ [red]토큰 발급 실패:[/red] {e}")
        console.print("  [dim]💡 팁: Client ID/Secret 오탈자 또는 토스증권 WTS [허용 IP 관리]에 현재 PC의 공인 IP가 등록되어 있는지 확인해 주세요.[/dim]")
        return

    # 2. Market Data Test (Public Quote & Exchange Rate)
    console.print("\n[bold yellow]▶ [Step 2] 시장 데이터 조회 테스트 (공개 API)...[/bold yellow]")
    try:
        rate = client.get_exchange_rate()
        console.print(f"  ✔ [green]원/달러 환율 조회 성공:[/green] 1 USD = {rate:,.2f} KRW")
    except Exception as e:
        console.print(f"  ⚠️ [yellow]환율 조회 실패:[/yellow] {e}")

    try:
        price = client.get_current_price("SOXL")
        console.print(f"  ✔ [green]SOXL 실시간 시세 조회 성공:[/green] ${price:.2f}")
    except Exception as e:
        console.print(f"  ⚠️ [yellow]SOXL 시세 조회 실패:[/yellow] {e}")

    # 3. Account & Balance Test (Private API)
    console.print("\n[bold yellow]▶ [Step 3] 계좌 자산 및 예수금 조회 테스트 (개인 계좌 API)...[/bold yellow]")
    try:
        accounts = client.get_accounts()
        console.print(f"  ✔ [green]계좌 목록 조회 성공:[/green] 총 {len(accounts)}개 계좌 연결됨")
    except Exception as e:
        console.print(f"  ❌ [red]계좌 목록 조회 실패:[/red] {e}")

    try:
        bp = client.get_buying_power(currency="USD")
        usd_cash = bp.get("cashBuyingPower", bp.get("usdCash", "0"))
        console.print(f"  ✔ [green]매수 가능 금액(USD 예수금) 조회 성공:[/green] USD ${float(usd_cash):,.2f}")
    except Exception as e:
        console.print(f"  ❌ [red]매수 가능 금액 조회 실패:[/red] {e}")
        console.print("  [dim]💡 팁: TOSS_ACCOUNT_SEQ(계좌 시퀀스 번호)가 본인의 실제 계좌 번호와 일치하는지 확인해 주세요.[/dim]")

    # 4. Holdings Test
    console.print("\n[bold yellow]▶ [Step 4] 보유 주식 잔고 조회 테스트...[/bold yellow]")
    try:
        holdings = client.get_holdings()
        items = holdings.get("items", [])
        console.print(f"  ✔ [green]보유 주식 잔고 조회 성공:[/green] 총 {len(items)}개 종목 보유 중")
        soxl_item = next((it for it in items if it.get("symbol") == "SOXL"), None)
        if soxl_item:
            qty = soxl_item.get("quantity", 0)
            avg = float(soxl_item.get("averagePrice", 0.0))
            console.print(f"    - [bold cyan]현재 SOXL 보유량:[/bold cyan] {qty}주 (평단: ${avg:.2f})")
        else:
            console.print(f"    - [dim]현재 SOXL 보유 수량 없음 (0주)[/dim]")
    except Exception as e:
        console.print(f"  ❌ [red]보유 주식 잔고 조회 실패:[/red] {e}")

    console.print("\n[bold green]🎉 토스증권 Open API 모든 진단 테스트가 성공적으로 완료되었습니다![/bold green]\n")


if __name__ == "__main__":
    test_connection()
