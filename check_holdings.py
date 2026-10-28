"""Quick holdings check using TossSecuritiesClient."""
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from services.toss_client import TossSecuritiesClient

client = TossSecuritiesClient()

holdings = client.get_holdings()
items = holdings.get("items", [])
print(f"보유 종목 수: {len(items)}개\n")

for item in items:
    symbol = item.get("symbol", "N/A")
    qty = item.get("quantity", 0)
    avg = item.get("averagePurchasePrice", item.get("averagePrice", "N/A"))
    try:
        current = client.get_current_price(symbol)
        current_str = f"${current:.2f}"
    except Exception:
        current_str = "조회실패"
    try:
        avg_f = float(avg)
        avg_str = f"${avg_f:.4f}"
    except Exception:
        avg_str = str(avg)
    print(f"{symbol}: {qty}주 | 평단 {avg_str} | 현재가 {current_str}")

bp = client.get_buying_power(currency="USD")
cash = bp.get("cashBuyingPower", bp.get("usdCash", "N/A"))
print(f"\nUSD 예수금: ${float(cash):,.2f}")

