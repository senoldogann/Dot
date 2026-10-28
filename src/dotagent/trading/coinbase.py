"""The owner's Coinbase account through the Advanced Trade API (docs/trading.md): its balances and market orders.

Each request is signed with the owner's CDP API key, an ECDSA (ES256) key Coinbase makes in its Developer Platform: a
JWT of two minutes for that one request, as Coinbase asks. The key is kept in the Mac's login Keychain (Settings ›
Trading saves it) and read only here; it never reaches a run, a log or an error. A key that may transfer money is
refused before any order: Dot has no withdrawal and needs none, so a key that could make one is a risk with no use.
"""

import asyncio
import base64
import json
import math
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from pydantic import SecretStr

from dotagent.core.errors import ToolExecutionError
from dotagent.trading.service import TradingRefused
from dotagent.trading.types import Fill, OrderRequest, ProductRules, Quote
from dotagent.vault.secretstore import SecretStore

API_HOST: str = "api.coinbase.com"
_BROKERAGE: str = "/api/v3/brokerage"
_JWT_LIFETIME_S: int = 120
# How long an order is followed until Coinbase says it is done; a market order is done in well under a second.
_FOLLOW_S: float = 15.0
_FOLLOW_EVERY_S: float = 0.5
_DONE: frozenset[str] = frozenset({"FILLED", "CANCELLED", "EXPIRED", "FAILED"})
KEY_ACCOUNT: str = "trading-coinbase"


class NothingFilled(TradingRefused):
    """Coinbase says an order it took ended having filled nothing: the only end of a sent order that surely did
    nothing."""


class CoinbaseError(ToolExecutionError):
    """Coinbase could not be reached, or answered something that is not what was asked for."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pem(text: str) -> bytes:
    """The key as Coinbase's file gives it, also when it was pasted with its line breaks written as ``\\n``."""
    return text.strip().replace("\\n", "\n").encode("ascii")


def load_key(private_key: str) -> ec.EllipticCurvePrivateKey:
    """The ECDSA P-256 key of ``private_key`` (PEM), or a :class:`CoinbaseError` saying why not, never what it holds."""
    try:
        key: object = serialization.load_pem_private_key(_pem(private_key), password=None)
    except (ValueError, TypeError, UnicodeEncodeError) as error:
        raise CoinbaseError(
            "the private key is not a PEM key: paste the whole key Coinbase gave, BEGIN and END lines too", {}
        ) from error
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise CoinbaseError(
            "the key is not an ECDSA (P-256) key: make the API key with the ECDSA signature algorithm in Coinbase's "
            "Developer Platform",
            {},
        )
    return key


def request_jwt(key_name: str, key: ec.EllipticCurvePrivateKey, method: str, path: str, now: int) -> str:
    """The JWT that signs one request (``method`` ``path`` on :data:`API_HOST`), valid for two minutes from ``now``."""
    header: dict[str, str] = {"alg": "ES256", "kid": key_name, "nonce": secrets.token_hex(16), "typ": "JWT"}
    payload: dict[str, object] = {
        "sub": key_name,
        "iss": "cdp",
        "nbf": now,
        "exp": now + _JWT_LIFETIME_S,
        "uri": f"{method} {API_HOST}{path}",
    }
    signing_input: str = (
        f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    der: bytes = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{signing_input}.{_b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def _amount(value: float, step: float) -> str:
    """``value`` rounded down to ``step``, written with as many decimals as ``step`` has, as Coinbase takes it."""
    decimals: int = max(0, -math.floor(math.log10(step) + 1e-9))
    floored: float = math.floor(value / step + 1e-9) * step
    return f"{floored:.{decimals}f}"


def _number(payload: Mapping[str, object], key: str) -> float:
    """A balance Coinbase gives: one that is not a finite number counts as nothing held."""
    value: float | None = _reported(payload, key)
    return value if value is not None and value > 0 else 0.0


def _reported(payload: Mapping[str, object], key: str) -> float | None:
    """The number Coinbase gives at ``key``, ``None`` when there is none or it is not a finite number."""
    raw: object = payload.get(key)
    try:
        value: float = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


async def kept_key(secrets_store: SecretStore) -> tuple[str, SecretStr] | None:
    """The Coinbase key kept in ``secrets_store`` (its name and private key), ``None`` while none is."""
    parts: dict[str, SecretStr] | None = await secrets_store.get(KEY_ACCOUNT)
    if parts is None or "name" not in parts or "private_key" not in parts:
        return None
    return parts["name"].get_secret_value(), parts["private_key"]


@dataclass(frozen=True)
class KeyPermissions:
    can_view: bool
    can_trade: bool
    can_transfer: bool


@dataclass
class CoinbaseBroker:
    """The owner's Coinbase account. ``key`` reads the kept key (its name and private key) when a request needs it;
    ``None`` while none is kept. ``transport`` replaces the network in tests."""

    key: Callable[[], Awaitable[tuple[str, SecretStr] | None]]
    timeout_s: float
    transport: httpx.AsyncBaseTransport | None = None
    clock: Callable[[], float] = time.time

    async def _signed(self) -> tuple[str, ec.EllipticCurvePrivateKey]:
        kept: tuple[str, SecretStr] | None = await self.key()
        if kept is None:
            raise TradingRefused("live trading has no Coinbase key: the owner saves one in Settings › Trading", {})
        name, private = kept
        return name, load_key(private.get_secret_value())

    async def _call(self, method: str, path: str, body: Mapping[str, object] | None = None) -> dict[str, object]:
        name, key = await self._signed()
        full: str = f"{_BROKERAGE}{path}"
        token: str = request_jwt(name, key, method, full.split("?")[0], int(self.clock()))
        try:
            async with httpx.AsyncClient(
                base_url=f"https://{API_HOST}", timeout=self.timeout_s, transport=self.transport, trust_env=False
            ) as client:
                response: httpx.Response = await client.request(
                    method, full, headers={"Authorization": f"Bearer {token}"}, json=body
                )
        except httpx.HTTPError as error:
            raise CoinbaseError("Coinbase could not be reached", {"error": type(error).__name__}) from error
        if response.status_code in (401, 403):
            raise TradingRefused(
                "Coinbase refused the key (expired, removed, or not allowed this): save a working one in Settings › "
                "Trading",
                {"status": response.status_code},
            )
        if response.status_code >= 400:
            raise CoinbaseError(
                "Coinbase answered with an error", {"status": response.status_code, "body": response.text[:300]}
            )
        try:
            payload: object = response.json()
        except ValueError as error:
            raise CoinbaseError("Coinbase answered with no JSON", {"path": path}) from error
        if not isinstance(payload, dict):
            raise CoinbaseError("Coinbase answered with something else than an object", {"path": path})
        return payload

    async def permissions(self) -> KeyPermissions:
        payload: dict[str, object] = await self._call("GET", "/key_permissions")
        return KeyPermissions(
            can_view=payload.get("can_view") is True,
            can_trade=payload.get("can_trade") is True,
            can_transfer=payload.get("can_transfer") is not False,
        )

    async def check_key(self) -> KeyPermissions:
        """The key's permissions, refused unless it may view and trade and may not transfer."""
        allowed: KeyPermissions = await self.permissions()
        if allowed.can_transfer:
            raise TradingRefused(
                "the Coinbase key may transfer money: Dot trades with a key that may view and trade only. Make one "
                "without the transfer permission and save it in Settings › Trading",
                {},
            )
        if not (allowed.can_view and allowed.can_trade):
            raise TradingRefused("the Coinbase key may not view and trade: give it both permissions", {})
        return allowed

    async def balances(self) -> dict[str, float]:
        payload: dict[str, object] = await self._call("GET", "/accounts?limit=250")
        accounts: object = payload.get("accounts")
        if not isinstance(accounts, list):
            raise CoinbaseError("Coinbase gave no accounts", {})
        held: dict[str, float] = {}
        for account in accounts:
            if not isinstance(account, dict):
                continue
            currency: object = account.get("currency")
            available: object = account.get("available_balance")
            if isinstance(currency, str) and isinstance(available, dict):
                amount: float = _number(available, "value")
                if amount > 0:
                    held[currency] = held.get(currency, 0.0) + amount
        return held

    async def execute(self, order_id: str, request: OrderRequest, quote: Quote, rules: ProductRules) -> Fill:
        try:
            await self.check_key()
        except CoinbaseError as error:
            # Nothing was sent: the order did nothing and does not count.
            raise TradingRefused(f"the key could not be checked with Coinbase: {error.message}", {}) from error
        if request.side == "buy":
            configuration: dict[str, str] = {"quote_size": _amount(request.quote_amount or 0.0, rules.quote_increment)}
        else:
            base: float = (
                request.base_amount if request.base_amount is not None else (request.quote_amount or 0.0) / quote.bid
            )
            configuration = {"base_size": _amount(base, rules.base_increment)}
        answer: dict[str, object] = await self._call(
            "POST",
            "/orders",
            {
                "client_order_id": order_id,
                "product_id": request.product,
                "side": request.side.upper(),
                "order_configuration": {"market_market_ioc": configuration},
            },
        )
        if answer.get("success") is not True:
            problem: object = answer.get("error_response")
            said: str = (
                str(problem.get("message") or problem.get("error") or problem.get("preview_failure_reason"))
                if isinstance(problem, dict)
                else "no reason given"
            )
            raise TradingRefused(f"Coinbase did not take the order: {said[:200]}", {})
        placed: object = answer.get("success_response")
        exchange_id: object = placed.get("order_id") if isinstance(placed, dict) else answer.get("order_id")
        if not isinstance(exchange_id, str) or not exchange_id:
            raise CoinbaseError("Coinbase took the order and gave no id of it", {})
        try:
            return await self._follow(exchange_id, request)
        except NothingFilled:
            raise
        except TradingRefused as error:
            # Taken by Coinbase, then the key failed (refused, or removed meanwhile): the order is on the market, so
            # what it did is not known and it stays counted (the security review of item 763).
            raise CoinbaseError(
                f"Coinbase took the order ({exchange_id}) and then could not be asked what it did: {error.message}. "
                "Look at the account before ordering again",
                {"order": exchange_id},
            ) from error

    async def _follow(self, exchange_id: str, request: OrderRequest) -> Fill:
        """What the order ``exchange_id`` filled at once Coinbase says it is done. One that filled nothing did nothing
        (:class:`TradingRefused`); one still under way after :data:`_FOLLOW_S` is an error, which keeps it counted."""
        deadline: float = time.monotonic() + _FOLLOW_S
        while True:
            payload: dict[str, object] = await self._call("GET", f"/orders/historical/{exchange_id}")
            order: object = payload.get("order")
            if isinstance(order, dict) and order.get("status") in _DONE:
                size: float | None = _reported(order, "filled_size")
                if size == 0:
                    raise NothingFilled(f"Coinbase's order ended {order.get('status')} and filled nothing", {})
                price: float | None = _reported(order, "average_filled_price")
                fee: float | None = _reported(order, "total_fees")
                if size is None or size < 0 or price is None or price <= 0 or fee is None or fee < 0:
                    # What it filled cannot be read, so it is not known: the order stays counted as it was judged
                    # (the pre-push review of item 763), never as nothing.
                    raise CoinbaseError(
                        f"Coinbase said the order {exchange_id} ended {order.get('status')} with no usable size, price "
                        "or fee: look at the account before ordering again",
                        {"order": exchange_id},
                    )
                return Fill(
                    order_id=exchange_id,
                    product=request.product,
                    side=request.side,
                    size=size,
                    price=price,
                    fee=fee,
                    ts=self.clock(),
                )
            if time.monotonic() > deadline:
                raise CoinbaseError(
                    "Coinbase has not said what the order did yet: look at the account before ordering again",
                    {"order": exchange_id},
                )
            await asyncio.sleep(_FOLLOW_EVERY_S)
