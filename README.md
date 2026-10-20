# 🚀 Auto Stock: SOXL 무한매수법 V4.0 멀티 에이전트 시스템

라오어의 **미국주식 무한매수법 V4.0** 알고리즘을 기반으로, **거시 매크로 분석 · 반도체 업황 사이클 · 종목 펀더멘털 · 리스크 워치독 · 토스증권 Open API 자동 체결 · 일일 투자 일지**가 결합된 7대 멀티 에이전트 자동화 시스템입니다.

---

## 🏗️ 7개 에이전트 시스템 구조

```
c:\Auto Stock\
├── agents/
│   ├── agent1_journal.py       # [Agent 1] 일일 체결 내역 & 투자 일지 마크다운 리포트 생성
│   ├── agent2_strategy.py      # [Agent 2] 무한매수법 V4.0 계산 엔진 & 백테스터 (주문표 산출)
│   ├── agent3_risk.py          # [Agent 3] 3배 레버리지 변동성 끌림(Decay) & 현금소진율 감시
│   ├── agent4_macro.py         # [Agent 4] 10년물 국채금리, DXY, VIX, 시장 유동성 분석
│   ├── agent5_industry.py      # [Agent 5] 반도체 지수(SOXX) 기술적 사이클 & RSI 진단
│   ├── agent6_fundamental.py   # [Agent 6] SOXL 편입 상위 기업(NVDA, AVGO 등) 밸류에이션 분석
│   └── agent7_execution.py     # [Agent 7] 토스증권 Open API 연동 및 모의/실거래 주문 집행
├── services/
│   └── toss_client.py          # 토스증권 공식 REST API & OAuth 2.0 클라이언트
├── config/
│   ├── settings.py             # 전략 설정 (40분할, 목표수익률 20%, 예산 $10,000 등)
│   └── toss_config.py          # 토스증권 API 엔드포인트 및 인증 설정
├── data/
│   ├── trade_history.json      # 영구 저장되는 누적 체결 및 계좌 포트폴리오 원장
│   └── reports/                # 매일 생성되는 일일 종합 투자 분석 보고서 (.md)
├── tests/                      # Pytest 기반 정밀 단위 테스트 슈트
├── main.py                     # 7개 에이전트 통합 파이프라인 오케스트레이터
├── requirements.txt            # 의존성 패키지
└── .env                        # 토스 API 키 및 보안 설정 (.gitignore 대상)
```

---

## 📈 무한매수법 V4.0 핵심 공식 구현

1. **원금 분할**: SOXL 기본 40분할 ($T = 0 \sim 40$), 목표수익률 20%
2. **1회 매수금**: $\text{남은 현금} \div (\text{분할수} - T)$
3. **별% (Star Ratio)**: $\text{목표\%} \times (1 - \frac{2 \times T}{\text{분할수}})$
4. **별지점 (Star Price)**: $\text{평단가} \times (1 + \text{별\%})$
5. **전반전 ($T < 20$)**: 1회 매수금 절반은 (별지점 - $0.01) LOC, 절반은 (평단가) LOC 매수
6. **후반전 ($T \ge 20$)**: 1회 매수금 전액을 (별지점 - $0.01) LOC 매수 (현금 보존 및 단가 하향)
7. **하방 사다리**: 급락장 체결을 위해 7~8개 라인의 하방 LOC 매수 주문 자동 배치
8. **익절 & 쿼터 매도**: 보유량의 1/4은 별지점 LOC(쿼터매도, $T \times 0.75$), 3/4은 평단가 대비 +20% 지정가 매도
9. **소진 & 리버스 모드**: $T \ge 39$ 도달 시 5일 이평선 기준 방어 매매 전환
10. **가격 기준**: 매수 상한은 가장 최근 완료된 일봉의 전일 종가 기준이며, 당일 진행 중인 시세를 종가로 대체하지 않습니다.
11. **토스 LOC 주문**: `LIMIT` 주문에 `timeInForce=CLS`를 지정해 장마감 지정가 주문으로 전송합니다.
12. **체결 정산**: 체결 수량·가격과 주문 유형을 주문 내역에서 읽어 T값을 갱신합니다. 계좌 조회 오류나 기준 종가 누락 시 오래된 값으로 주문표를 만들지 않습니다.

---

## 💻 사용 가이드

### 1. 일일 무한매수 파이프라인 실행 (모의 실행 / Dry-Run 기본)
```powershell
.\.venv\Scripts\python.exe main.py --mode run
```
- 7개 에이전트가 순차적으로 실행되어 최근 완료된 SOXL 일봉 종가와 계좌 상태를 기준으로 주문표를 산출하고, 리스크를 진단한 뒤 모의 주문을 접수합니다.
- `data/reports/YYYY-MM-DD_SOXL_report.md`에 당일 일지가 자동 생성됩니다.

### 2. Linux 서버에서 평일 오전 9시 / 저녁 8시 자동 실행
```bash
./.venv/bin/python morning_routine.py
```

GitHub Actions 예약 대신 서버의 systemd 타이머를 사용합니다. GitHub Actions의 수동 `workflow_dispatch`는 계속 사용할 수 있습니다.

1. 변경사항을 `main` 브랜치에 push한 후 Ubuntu 서버에서 저장소를 clone합니다.
   ```bash
   git clone https://github.com/jousinKaka/Auto-Stock.git /home/ubuntu/auto-stock
   cd /home/ubuntu/auto-stock
   ```
2. 서버 시간을 한국 시간으로 설정하고, 인증 정보를 보관할 권한 제한 파일을 만든 뒤 편집합니다.
   ```bash
   sudo timedatectl set-timezone Asia/Seoul
   sudo install -d -o root -g root -m 700 /etc/auto-stock
   sudo install -o root -g root -m 600 .env.example /etc/auto-stock/auto-stock.env
   sudo nano /etc/auto-stock/auto-stock.env
   ```
   예시 파일의 placeholder를 실제 값으로 바꾸세요. `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`, `TOSS_ACCOUNT_SEQ`, `TOTAL_BUDGET_USD`는 설정해야 합니다. 텔레그램 알림을 사용하면 `TELEGRAM_BOT_TOKEN`과 `TELEGRAM_CHAT_ID`도 설정합니다. 처음에는 `ENABLE_LIVE_TRADING=false`로 두세요.
3. 저장소 루트에서 설치 스크립트를 실행합니다.
   ```bash
   bash deploy/install_systemd_timers.sh
   ```
   스크립트가 Python 가상환경과 의존성을 준비하고, systemd 타이머를 설치 및 활성화합니다.
4. 타이머 시간과 실행 로그를 확인합니다.
   ```bash
   systemctl list-timers auto-stock-morning.timer auto-stock-evening.timer
   journalctl -u auto-stock@morning.service -n 100 --no-pager
   journalctl -u auto-stock@evening.service -n 100 --no-pager
   ```

아침 루틴은 항상 Dry-Run이며, 전일 야간 체결 정산·주문표 생성·리포트 저장·텔레그램 발송을 합니다. 저녁도 기본은 Dry-Run입니다. 저녁 실거래를 허용하려면 서버 환경 파일에서 `ENABLE_LIVE_TRADING=true`와 양수 `LIVE_BUY_LIMIT_USD`를 설정해야 합니다. 이 경우 기존 실거래 흐름대로 매수·매도 계획을 제출할 수 있으므로, 계좌와 주문표 및 한도를 먼저 검토하세요. 두 루틴은 서버 현지 시간 기준 평일 09:00 및 20:00에 실행됩니다.

설치 후 실행은 서버의 저장소와 장부에 반영되며 GitHub에 자동 commit/push하지는 않습니다. 코드를 갱신할 때는 서버에서 `git pull --ff-only origin main` 후 설치 스크립트를 다시 실행하세요.

### 3. GitHub Actions live-trading safeguards
- GitHub Actions no longer has scheduled runs. The workflow can still be started manually; it defaults to Dry-Run. A manual live run requires explicitly checking `confirm_live_buy_only` in the workflow dispatch form.
- Manual live mode additionally requires the repository Actions variable `ENABLE_LIVE_TRADING` to be exactly `true` and a positive `LIVE_BUY_LIMIT_USD`; the full planned buy notional must fit under that limit and within Toss USD buying power before any order is sent.
- Before sending, the executor blocks on any open SOXL order and any same-day closed SOXL buy with a positive filled quantity; canceled/unfilled closed orders and closed sells do not block. It also checks current holdings for sells and creates a persistent per-day lock under `AUTO_STOCK_STATE_DIR`. Failed preflight or a repeated attempt blocks the plan. Inspect Toss order history before manually removing a lock.
- Live order submission is sequential and cannot be rolled back atomically. If a broker/network error occurs after some submissions, inspect Toss order history before any retry.
- Do not enable live trading until the account, generated order sheet, and configured buy limit have been reviewed.
- For the currently recorded strategy cycle only, `cash_basis_cycle` calculates strategy cash as `TOTAL_BUDGET_USD - (SOXL shares × average price)` instead of replacing it with Toss buying power. The current ledger is initialized at approximately $18,180 against the $30,000 budget. Reports show this modeled strategy cash separately from actual Toss USD buying power; live-order preflight still checks the actual broker buying power. The cash basis mode ends automatically when the recorded cycle changes.

### 4. 텔레그램 알림 연동 테스트
```powershell
.\.venv\Scripts\python.exe test_telegram.py
```

### 5. 과거 2~5년 무한매수법 V4.0 백테스트
```powershell
.\.venv\Scripts\python.exe main.py --mode backtest --years 2
```

### 6. 단위 테스트 검증
```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

---

## 🔑 환경 변수(.env) 설정 가이드

### 1. 토스증권 Open API 설정
1. 토스증권 WTS (PC 웹) 접속 후 **[설정] > [Open API]**에서 `Client ID`와 `Client Secret` 발급
2. **[허용 IP 관리]**에 현재 PC의 외부 IP 등록
3. `.env` 파일에 입력:
```env
TOSS_CLIENT_ID=발급받은_클라이언트_ID
TOSS_CLIENT_SECRET=발급받은_클라이언트_SECRET
TOSS_ACCOUNT_SEQ=토스증권_계좌번호_또는_시퀀스
```

### 2. 텔레그램 봇 알림 설정 (매일 아침 9시 모닝 리포트 수신)
1. 텔레그램 앱에서 `@BotFather` 검색 후 `/newbot`으로 봇 생성 및 API Token 복사
2. 생성한 내 봇 채팅방에 들어가서 `/start` 누르기 (필수)
3. 내 Chat ID 확인: 텔레그램에서 `@userinfobot` 검색 후 `/start` -> Id 숫자 복사
4. `.env` 파일에 입력:
```env
TELEGRAM_BOT_TOKEN=발급받은_봇_토큰
TELEGRAM_CHAT_ID=내_Chat_ID_숫자
```
