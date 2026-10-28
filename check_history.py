import json
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

with open("data/trade_history.json", encoding="utf-8") as f:
    data = json.load(f)

print(f"총 {len(data)}개 레코드\n")
for r in data:
    print(
        f"[{r['date']}] 회차#{r['cycle']} | "
        f"T={r['T_before']:.2f}->{r['T_after']:.2f} | "
        f"보유량 {r['total_shares_before']}->{r['total_shares_after']}주 | "
        f"평단 ${r['avg_price_before']:.4f}->${r['avg_price_after']:.4f} | "
        f"매수 {r['bought_shares']}주 / 매도 {r['sold_shares']}주"
    )
    broker_shares = r.get("broker_soxl_shares")
    broker_avg = r.get("broker_soxl_avg_price")
    if broker_shares is not None:
        print(f"   └ 토스실계좌: {broker_shares}주 @ ${broker_avg}")
    notes = r.get("notes", [])
    for n in notes:
        print(f"   └ {n}")
    print()

