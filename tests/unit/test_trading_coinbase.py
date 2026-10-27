"""The owner's Coinbase account (docs/trading.md): each request signed as Coinbase asks (an ES256 JWT of two minutes for
that one request), a key that may transfer money refused before any order, and a market order followed until Coinbase
says what it filled. Coinbase is a stand-in (``httpx.MockTransport``) and the key is made here: no request leaves the
Mac, and no Keychain is read."""

import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from pydantic import SecretStr

from dotagent.config import TradingConfig, load_default_config
from dotagent.store.db import Store
from dotagent.store.models import TradingOrderRecord
from dotagent.trading.coinbase import CoinbaseBroker, CoinbaseError, load_key, request_jwt
from dotagent.trading.market import CoinbaseMarket
from dotagent.trading.service import Trading, TradingRefused, TradingSettings
from dotagent.trading.types import OrderRequest, ProductRules, Quote

_NAME: str = "organizations/org-1/apiKeys/key-1"


def _unpadded(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _pem(key: ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def _verified(token: str, public: ec.EllipticCurvePublicKey) -> tuple[dict[str, object], dict[str, object]]:
    header, payload, signature = token.split(".")
    raw: bytes = _unpadded(signature)
    der: bytes = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    public.verify(der, f"{header}.{payload}".encode(), ec.ECDSA(hashes.SHA256()))
    return json.loads(_unpadded(header)), json.loads(_unpadded(payload))


def test_a_request_is_signed_for_its_own_method_and_path_for_two_minutes() -> None:
    key: ec.EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
    token: str = request_jwt(_NAME, key, "POST", "/api/v3/brokerage/orders", 1_793_000_000)
    header, payload = _verified(token, key.public_key())
    assert (header["alg"], header["kid"], header["typ"]) == ("ES256", _NAME, "JWT") and len(str(header["nonce"])) == 32
    assert payload == {
        "sub": _NAME, "iss": "cdp", "nbf": 1_793_000_000, "exp": 1_793_000_120,
        "uri": "POST api.coinbase.com/api/v3/brokerage/orders",
    }  # fmt: skip
    other: ec.EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(InvalidSignature):
        _verified(token, other.public_key())


def test_the_key_is_read_as_coinbase_gives_it_and_another_kind_is_refused_without_saying_what_it_holds() -> None:
    key: ec.EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
    pasted: str = _pem(key).replace("\n", "\\n")
    assert load_key(pasted).private_numbers() == key.private_numbers()
    with pytest.raises(CoinbaseError, match="not an ECDSA"):
        load_key(_pem(ed25519.Ed25519PrivateKey.generate()))
    with pytest.raises(CoinbaseError, match="not a PEM key") as failed:
        load_key("not a key at all")
    assert "not a key at all" not in str(failed.value)


class _Coinbase:
    """A stand-in of Coinbase's Advanced Trade API: it checks each request's signature and keeps what was asked."""

    def __init__(self, key: ec.EllipticCurvePrivateKey, transfer: bool = False, accept: bool = True) -> None:
        self.price: str = "50010.5"
        self.public: ec.EllipticCurvePublicKey = key.public_key()
        self.transfer: bool = transfer
        self.accept: bool = accept
        self.orders: list[dict[str, object]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        token: str = request.headers["Authorization"].removeprefix("Bearer ")
        _, payload = _verified(token, self.public)
        assert payload["uri"] == f"{request.method} api.coinbase.com{request.url.path}"
        path: str = request.url.path.removeprefix("/api/v3/brokerage")
        body: dict[str, object]
        if path == "/key_permissions":
            body = {"can_view": True, "can_trade": True, "can_transfer": self.transfer}
        elif path == "/accounts":
            body = {"accounts": [
                {"currency": "EUR", "available_balance": {"value": "400.50", "currency": "EUR"}},
                {"currency": "BTC", "available_balance": {"value": "0.002", "currency": "BTC"}},
            ]}  # fmt: skip
        elif path == "/orders" and request.method == "POST":
            self.orders.append(json.loads(request.content))
            body = (
                {"success": True, "success_response": {"order_id": "cb-1"}}
                if self.accept
                else {"success": False, "error_response": {"message": "Insufficient balance in source account"}}
            )
        elif path == "/orders/historical/cb-1":
            body = {"order": {
                "order_id": "cb-1", "status": "FILLED", "filled_size": "0.00049", "average_filled_price": self.price,
                "total_fees": "0.15",
            }}  # fmt: skip
        else:
            return httpx.Response(404)
        return httpx.Response(200, content=json.dumps(body).encode())


def _broker(key: ec.EllipticCurvePrivateKey, coinbase: _Coinbase) -> CoinbaseBroker:
    async def kept() -> tuple[str, SecretStr] | None:
        return _NAME, SecretStr(_pem(key))

    return CoinbaseBroker(key=kept, timeout_s=5.0, transport=httpx.MockTransport(coinbase), clock=lambda: 1_793_000_000)


_RULES: ProductRules = ProductRules(
    product="BTC-EUR", base_increment=1e-8, quote_increment=0.01, quote_min_size=1.0, base_min_size=1e-8, trading=True
)
_QUOTE: Quote = Quote(product="BTC-EUR", bid=50000.0, ask=50010.0, last=50005.0, ts=0.0)


async def test_a_market_buy_is_sent_in_coinbases_steps_and_followed_to_what_it_filled() -> None:
    key: ec.EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
    coinbase: _Coinbase = _Coinbase(key)
    broker: CoinbaseBroker = _broker(key, coinbase)
    assert await broker.balances() == {"EUR": 400.5, "BTC": 0.002}
    buy: OrderRequest = OrderRequest(product="BTC-EUR", side="buy", quote_amount=24.999, base_amount=None)
    fill = await broker.execute("ord_1", buy, _QUOTE, _RULES)
    assert coinbase.orders == [{
        "client_order_id": "ord_1", "product_id": "BTC-EUR", "side": "BUY",
        "order_configuration": {"market_market_ioc": {"quote_size": "24.99"}},
    }]  # fmt: skip
    assert (fill.order_id, fill.size, fill.price, fill.fee) == ("cb-1", 0.00049, 50010.5, 0.15)
    sell: OrderRequest = OrderRequest(product="BTC-EUR", side="sell", quote_amount=None, base_amount=0.0012345678912)
    await broker.execute("ord_2", sell, _QUOTE, _RULES)
    assert coinbase.orders[-1]["order_configuration"] == {"market_market_ioc": {"base_size": "0.00123456"}}


async def test_a_key_that_may_transfer_money_places_no_order_and_a_refused_order_did_nothing() -> None:
    key: ec.EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
    buy: OrderRequest = OrderRequest(product="BTC-EUR", side="buy", quote_amount=10.0, base_amount=None)
    transferring: _Coinbase = _Coinbase(key, transfer=True)
    with pytest.raises(TradingRefused, match="may transfer money"):
        await _broker(key, transferring).execute("ord_1", buy, _QUOTE, _RULES)
    assert transferring.orders == []
    refusing: _Coinbase = _Coinbase(key, accept=False)
    with pytest.raises(TradingRefused, match="Insufficient balance"):
        await _broker(key, refusing).execute("ord_1", buy, _QUOTE, _RULES)


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    opened: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "shots")
    yield opened
    await opened.close()


async def test_a_live_order_is_kept_with_coinbases_id_and_a_refused_one_does_not_count(
    store: Store, tmp_path: Path
) -> None:
    def market(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ticker"):
            return httpx.Response(200, json={"trades": [{"price": "50005"}], "best_bid": "50000", "best_ask": "50010"})
        return httpx.Response(200, json={
            "status": "online", "trading_disabled": False, "is_disabled": False, "cancel_only": False,
            "limit_only": False, "base_increment": "0.00000001", "quote_increment": "0.01", "quote_min_size": "1",
            "base_min_size": "0.00000001",
        })  # fmt: skip

    key: ec.EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
    coinbase: _Coinbase = _Coinbase(key)
    broker: CoinbaseBroker = _broker(key, coinbase)
    config: TradingConfig = load_default_config().trading.model_copy(update={"mode": "live", "products": ["BTC-EUR"]})
    trading: Trading = Trading(
        store=store,
        settings=TradingSettings(config),
        market=CoinbaseMarket(5.0, httpx.MockTransport(market)),
        zone=ZoneInfo("Europe/Helsinki"),
        failsafe_file=tmp_path / "STOP",
        live=lambda: broker,
    )
    order: TradingOrderRecord = await trading.place(
        OrderRequest(product="BTC-EUR", side="buy", quote_amount=20.0, base_amount=None), "run_a", "live"
    )
    assert (order.mode, order.status, order.exchange_id) == ("live", "filled", "cb-1")
    coinbase.accept = False
    with pytest.raises(TradingRefused):
        await trading.place(
            OrderRequest(product="BTC-EUR", side="buy", quote_amount=20.0, base_amount=None), "run_a", "live"
        )
    statuses: list[str] = [one.status for one in await store.trading_orders("live", 0.0, 10)]
    assert statuses == ["failed", "filled"]
    assert await store.trading_traded_since("live", 0.0) == pytest.approx(order.notional)


async def test_an_order_coinbase_took_stays_counted_when_the_key_fails_while_it_is_followed(
    store: Store, tmp_path: Path
) -> None:
    """The security review of item 763: a key refused (or removed) after Coinbase took the order made the order
    ``failed``, out of the day's count, and told the run it was not placed. It is ``unknown`` now, and counts."""

    def market(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ticker"):
            return httpx.Response(200, json={"trades": [{"price": "50005"}], "best_bid": "50000", "best_ask": "50010"})
        return httpx.Response(200, json={
            "status": "online", "trading_disabled": False, "is_disabled": False, "cancel_only": False,
            "limit_only": False, "base_increment": "0.00000001", "quote_increment": "0.01", "quote_min_size": "1",
            "base_min_size": "0.00000001",
        })  # fmt: skip

    key: ec.EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
    coinbase: _Coinbase = _Coinbase(key)

    def refusing_the_follow(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v3/brokerage/orders/historical/"):
            return httpx.Response(401)
        return coinbase(request)

    broker: CoinbaseBroker = _broker(key, coinbase)
    broker.transport = httpx.MockTransport(refusing_the_follow)
    config: TradingConfig = load_default_config().trading.model_copy(update={"mode": "live", "products": ["BTC-EUR"]})
    trading: Trading = Trading(
        store=store,
        settings=TradingSettings(config),
        market=CoinbaseMarket(5.0, httpx.MockTransport(market)),
        zone=ZoneInfo("Europe/Helsinki"),
        failsafe_file=tmp_path / "STOP",
        live=lambda: broker,
    )
    with pytest.raises(CoinbaseError, match="took the order"):
        await trading.place(
            OrderRequest(product="BTC-EUR", side="buy", quote_amount=20.0, base_amount=None), "r", "live"
        )
    (kept,) = await store.trading_orders("live", 0.0, 10)
    assert kept.status == "unknown" and await store.trading_traded_since("live", 0.0) == pytest.approx(20.0)


async def test_a_fill_coinbase_tells_with_no_usable_price_is_not_taken_for_nothing() -> None:
    """The pre-push review of item 763: a price missing, empty or not finite was read as 0, and the order counted for
    nothing against the day. It is an error now, which keeps the order unknown and counted as it was judged."""
    key: ec.EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
    buy: OrderRequest = OrderRequest(product="BTC-EUR", side="buy", quote_amount=10.0, base_amount=None)
    for price in ("", "nan", "inf", "0"):
        coinbase: _Coinbase = _Coinbase(key)
        coinbase.price = price
        with pytest.raises(CoinbaseError, match="no usable size, price or fee"):
            await _broker(key, coinbase).execute("ord_1", buy, _QUOTE, _RULES)


async def test_a_key_coinbase_cannot_be_asked_about_sends_nothing_and_the_order_did_nothing() -> None:
    """The pre-push review of item 763: an error before the order is sent (Coinbase out of reach while the key is
    checked) is a refusal, so the order is ``failed`` and does not count, not ``unknown``."""
    key: ec.EllipticCurvePrivateKey = ec.generate_private_key(ec.SECP256R1())
    coinbase: _Coinbase = _Coinbase(key)

    def down(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/key_permissions"):
            return httpx.Response(503, text="unavailable")
        return coinbase(request)

    broker: CoinbaseBroker = _broker(key, coinbase)
    broker.transport = httpx.MockTransport(down)
    buy: OrderRequest = OrderRequest(product="BTC-EUR", side="buy", quote_amount=10.0, base_amount=None)
    with pytest.raises(TradingRefused, match="could not be checked"):
        await broker.execute("ord_1", buy, _QUOTE, _RULES)
    assert coinbase.orders == []
