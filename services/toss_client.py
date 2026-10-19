"""Toss Securities OpenAPI Client.

Handles OAuth 2.0 token management, account holdings, buying power,
market calendar, price quotes, and order lifecycle management.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel

from config.toss_config import toss_config


class TossToken(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 86400
    issued_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        # Refresh 5 minutes before expiration
        return (time.time() - self.issued_at) >= (self.expires_in - 300)


class TossSecuritiesClient:
    """Official REST Client for Toss Securities OpenAPI."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        account_seq: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.client_id = client_id or toss_config.toss_client_id
        self.client_secret = client_secret or toss_config.toss_client_secret
        self.account_seq = account_seq or toss_config.toss_account_seq
        self.base_url = (base_url or toss_config.toss_base_url).rstrip("/")
        
        self._token: Optional[TossToken] = None
        self.http_client = httpx.Client(timeout=15.0)

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.account_seq)

    def get_access_token(self, force_refresh: bool = False) -> str:
        """Obtain or refresh OAuth 2.0 access token via Client Credentials Grant."""
        if not self.is_configured:
            raise ValueError("Toss Securities API credentials are not configured in .env file.")

        if self._token and not self._token.is_expired and not force_refresh:
            return self._token.access_token

        url = f"{self.base_url}/oauth2/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        resp = self.http_client.post(url, headers=headers, data=data)
        if resp.status_code != 200:
            raise RuntimeError(f"Toss OAuth Token issuance failed [{resp.status_code}]: {resp.text}")

        res_json = resp.json()
        self._token = TossToken(
            access_token=res_json["access_token"],
            token_type=res_json.get("token_type", "Bearer"),
            expires_in=res_json.get("expires_in", 86400),
            issued_at=time.time(),
        )
        return self._token.access_token

    def _resolve_account_seq(self) -> str:
        """Auto-resolve accountSeq if user provided an 11-digit account number with or without hyphens."""
        clean_input = (self.account_seq or "").replace("-", "").strip()
        if clean_input.isdigit() and len(clean_input) < 5:
            self.account_seq = clean_input
            return self.account_seq
        
        try:
            accounts = self.get_accounts()
            if accounts:
                for acc in accounts:
                    acc_no_clean = str(acc.get("accountNo", "")).replace("-", "").strip()
                    if acc_no_clean == clean_input:
                        self.account_seq = str(acc.get("accountSeq", 1))
                        return self.account_seq
                # Default to first account sequence
                self.account_seq = str(accounts[0].get("accountSeq", 1))
                return self.account_seq
        except Exception:
            pass
        return "1"

    def _get_headers(self, require_account: bool = True) -> Dict[str, str]:
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if require_account:
            headers["X-Tossinvest-Account"] = self._resolve_account_seq()
        return headers

    # ==========================================
    # Account & Asset Endpoints
    # ==========================================

    def get_accounts(self) -> List[Dict[str, Any]]:
        """GET /api/v1/accounts - Retrieve list of user accounts."""
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/api/v1/accounts"
        resp = self.http_client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data.get("result", [])
        return data if isinstance(data, list) else []

    def get_holdings(self) -> Dict[str, Any]:
        """GET /api/v1/holdings - Retrieve stock holdings and valuation."""
        headers = self._get_headers(require_account=True)
        url = f"{self.base_url}/api/v1/holdings"
        resp = self.http_client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {}) if isinstance(data, dict) else {}

    def get_buying_power(self, currency: str = "USD") -> Dict[str, Any]:
        """GET /api/v1/buying-power - Retrieve available cash buying power (USD/KRW)."""
        headers = self._get_headers(require_account=True)
        url = f"{self.base_url}/api/v1/buying-power"
        resp = self.http_client.get(url, headers=headers, params={"currency": currency})
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {}) if isinstance(data, dict) else {}

    # ==========================================
    # Market Data Endpoints
    # ==========================================

    def get_current_price(self, symbol: str = "SOXL") -> float:
        """GET /api/v1/prices - Retrieve current price for given symbol."""
        headers = self._get_headers(require_account=False)
        url = f"{self.base_url}/api/v1/prices"
        resp = self.http_client.get(url, headers=headers, params={"symbols": symbol})
        resp.raise_for_status()
        data = resp.json()
        items = data.get("result", []) if isinstance(data, dict) else data
        if isinstance(items, list) and items:
            item = items[0]
            val = item.get("lastPrice", item.get("price", 0.0))
            return float(val) if val else 0.0
        return 0.0

    def get_exchange_rate(self, base: str = "USD", quote: str = "KRW") -> float:
        """GET /api/v1/exchange-rate - Retrieve KRW/USD exchange rate."""
        headers = self._get_headers(require_account=False)
        url = f"{self.base_url}/api/v1/exchange-rate"
        resp = self.http_client.get(url, headers=headers, params={"baseCurrency": base, "quoteCurrency": quote})
        resp.raise_for_status()
        data = resp.json()
        res = data.get("result", {}) if isinstance(data, dict) else data
        return float(res.get("rate", 1400.0))

    def get_us_market_calendar(self) -> Dict[str, Any]:
        """GET /api/v1/market-calendar/US - Retrieve US market trading hours."""
        headers = self._get_headers(require_account=False)
        url = f"{self.base_url}/api/v1/market-calendar/US"
        resp = self.http_client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ==========================================
    # Order Execution Endpoints
    # ==========================================

    def create_order(
        self,
        symbol: str,
        side: str,         # "BUY" | "SELL"
        order_type: str,   # "LIMIT" | "MARKET"
        quantity: int,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None,
        time_in_force: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /api/v1/orders - Place a new stock order."""
        headers = self._get_headers(require_account=True)
        url = f"{self.base_url}/api/v1/orders"
        
        cid = client_order_id or f"soxl-{side.lower()}-{uuid.uuid4().hex[:12]}"
        body: Dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "orderType": order_type.upper(),
            "quantity": str(quantity),
            "clientOrderId": cid,
        }
        if order_type.upper() == "LIMIT":
            if price is None or price <= 0:
                raise ValueError("Price is required for LIMIT order")
            body["price"] = f"{price:.2f}"
        if time_in_force is not None:
            body["timeInForce"] = time_in_force.upper()

        resp = self.http_client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()

    def get_order_history(
        self,
        status: str,
        symbol: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """GET /api/v1/orders - Retrieve a page of open or closed orders."""
        headers = self._get_headers(require_account=True)
        url = f"{self.base_url}/api/v1/orders"
        params: Dict[str, Any] = {"status": status, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        if cursor:
            params["cursor"] = cursor
        resp = self.http_client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {}) if isinstance(data, dict) else {}

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """POST /api/v1/orders/{orderId}/cancel - Cancel an open order."""
        headers = self._get_headers(require_account=True)
        url = f"{self.base_url}/api/v1/orders/{order_id}/cancel"
        resp = self.http_client.post(url, headers=headers)
        resp.raise_for_status()
        return resp.json()
