"""Cliente do GeckoTerminal (leitura pública, sem chave) — o universo do SCAN de memecoins.

O guia usa GeckoTerminal + FOMO `filterTokens`. A API do FOMO não é pública, então tudo vem daqui:
pools (preço, liquidez, volume, compras/vendas por janela) e info do token (holders, concentração, honeypot).
Plano grátis: "~10 chamadas por minuto", mas na prática o 429 veio na 9ª chamada em 50 s (29/10/2026). Cada chamada
espera a vez (MIN_INTERVAL_S, 5 por minuto) e um 429 pausa todo mundo até o Retry-After.

Nome, símbolo, descrição e links do token são texto de terceiros: só o símbolo é guardado, limpo, para o painel.
Nada disso vai para o Grok ou o Jev (eles recebem rótulos C1..Cn e números).
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx

BASE_URL = "https://api.geckoterminal.com/api/v2"
HEADERS = {"Accept": "application/json;version=20230302"}
MIN_INTERVAL_S = 12.0  # 5 chamadas/min: com 6,5 s o plano grátis devolveu 429 na 9ª chamada
NETWORKS = ("solana", "bsc")
WINDOWS = ("m5", "m15", "m30", "h1", "h6", "h24")
ADDRESS_RE = re.compile(r"^(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")
SYMBOL_CLEAN = re.compile(r"[^A-Za-z0-9._$-]")


class GeckoError(RuntimeError):
    pass


class GeckoRateLimited(GeckoError):
    def __init__(self, detail: str, retry_after: float):
        super().__init__(detail)
        self.retry_after = retry_after


@dataclass(frozen=True)
class Pool:
    net: str
    address: str
    token: str  # endereço do token base
    symbol: str  # só para exibir; nunca vai para LLM
    created_ms: int
    price_usd: float
    liquidity_usd: float
    mcap_usd: float  # market cap; sem ele, FDV (mcap_is_fdv)
    mcap_is_fdv: bool
    volume: dict[str, float] = field(default_factory=dict)
    change: dict[str, float] = field(default_factory=dict)  # variação de preço em %, por janela
    txns: dict[str, dict[str, int]] = field(default_factory=dict)  # buys, sells, buyers, sellers por janela

    @property
    def key(self) -> str:
        return f"{self.net}:{self.address}"

    def label(self) -> str:
        return f"{self.symbol} ({self.net})"


@dataclass(frozen=True)
class TokenInfo:
    holders: int | None
    holders_updated_ms: int | None
    top10_pct: float | None
    honeypot: bool | None
    mint_authority: bool | None
    freeze_authority: bool | None


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _ms(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _flag(v) -> bool | None:
    """GeckoTerminal devolve "yes"/"no", booleano ou o endereço da autoridade."""
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    t = str(v).strip().lower()
    if t in ("", "unknown", "null", "none"):
        return None
    return t not in ("no", "false", "0")


def clean_symbol(raw) -> str:
    s = SYMBOL_CLEAN.sub("", str(raw or ""))[:16]
    return s or "?"


def parse_pools(body: dict, net: str) -> list[Pool]:
    symbols = {}
    for inc in body.get("included") or []:
        if inc.get("type") == "token":
            symbols[inc.get("id")] = clean_symbol((inc.get("attributes") or {}).get("symbol"))
    out = []
    for item in body.get("data") or []:
        a = item.get("attributes") or {}
        address = str(a.get("address") or "")
        base_id = (((item.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id") or ""
        token = base_id.split("_", 1)[1] if "_" in base_id else ""
        created = _ms(a.get("pool_created_at"))
        price = _num(a.get("base_token_price_usd"))
        if not ADDRESS_RE.match(address) or not ADDRESS_RE.match(token) or created is None or not price:
            continue  # endereço fora do padrão ou sem preço: descarta, nunca adivinha
        mcap = _num(a.get("market_cap_usd"))
        fdv = _num(a.get("fdv_usd"))
        txns = {}
        for w in WINDOWS:
            t = (a.get("transactions") or {}).get(w) or {}
            txns[w] = {k: int(_num(t.get(k)) or 0) for k in ("buys", "sells", "buyers", "sellers")}
        out.append(Pool(
            net=net, address=address, token=token, symbol=symbols.get(base_id, "?"), created_ms=created,
            price_usd=price, liquidity_usd=_num(a.get("reserve_in_usd")) or 0.0,
            mcap_usd=mcap if mcap else (fdv or 0.0), mcap_is_fdv=not mcap,
            volume={w: _num((a.get("volume_usd") or {}).get(w)) or 0.0 for w in WINDOWS},
            change={w: _num((a.get("price_change_percentage") or {}).get(w)) or 0.0 for w in WINDOWS},
            txns=txns))
    return out


def parse_info(body: dict) -> TokenInfo:
    a = (body.get("data") or {}).get("attributes") or {}
    h = a.get("holders") or {}
    dist = h.get("distribution_percentage") or {}
    count = _num(h.get("count"))
    return TokenInfo(holders=int(count) if count is not None else None, holders_updated_ms=_ms(h.get("last_updated")),
                     top10_pct=_num(dist.get("top_10")), honeypot=_flag(a.get("is_honeypot")),
                     mint_authority=_flag(a.get("mint_authority")), freeze_authority=_flag(a.get("freeze_authority")))


class GeckoTerminal:
    """Thread-safe: o SCAN roda numa thread e a checagem do CHIEF na principal; os dois dividem a cota."""

    def __init__(self, base_url: str = BASE_URL, timeout: float = 20.0, transport: httpx.BaseTransport | None = None,
                 min_interval: float = MIN_INTERVAL_S, clock=time.monotonic, sleep=time.sleep):
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport, headers=HEADERS)
        self._lock = threading.Lock()
        self._next = 0.0
        self.min_interval = min_interval
        self.clock = clock
        self.sleep = sleep
        self.calls = 0

    def _get(self, path: str, params: dict | None = None) -> dict:
        with self._lock:
            wait = self._next - self.clock()
            if wait > 0:
                self.sleep(wait)
            self._next = self.clock() + self.min_interval
            try:
                r = self._http.get(path, params=params)
            except httpx.TransportError as e:
                raise GeckoError(f"falha de conexão: {type(e).__name__}") from e
            self.calls += 1
            if r.status_code == 429:
                try:
                    retry = float(r.headers.get("Retry-After") or 60)
                except ValueError:
                    retry = 60.0
                self._next = self.clock() + retry  # ninguém chama de novo antes disso
                raise GeckoRateLimited("HTTP 429 (limite do plano grátis)", retry)
            if r.status_code >= 400:
                raise GeckoError(f"HTTP {r.status_code}")
            try:
                body = r.json()
            except ValueError as e:
                raise GeckoError("resposta não é JSON") from e
            if not isinstance(body, dict):
                raise GeckoError("resposta fora do formato")
            return body

    @staticmethod
    def _net(net: str) -> str:
        if net not in NETWORKS:
            raise ValueError(f"rede não suportada: {net}")
        return net

    def trending(self, net: str, duration: str = "1h") -> list[Pool]:
        if duration not in ("5m", "1h", "6h", "24h"):
            raise ValueError(duration)
        body = self._get(f"/networks/{self._net(net)}/trending_pools", {"duration": duration, "include": "base_token"})
        return parse_pools(body, net)

    def top_volume(self, net: str) -> list[Pool]:
        body = self._get(f"/networks/{self._net(net)}/pools", {"sort": "h24_volume_usd_desc", "include": "base_token"})
        return parse_pools(body, net)

    def new_pools(self, net: str) -> list[Pool]:
        return parse_pools(self._get(f"/networks/{self._net(net)}/new_pools", {"include": "base_token"}), net)

    def pools(self, net: str, addresses: list[str]) -> list[Pool]:
        """Até 30 pools numa chamada (o RISK vigia as posições abertas com isso)."""
        addrs = [a for a in addresses if ADDRESS_RE.match(a)][:30]
        if not addrs:
            return []
        body = self._get(f"/networks/{self._net(net)}/pools/multi/{','.join(addrs)}", {"include": "base_token"})
        return parse_pools(body, net)

    def token_info(self, net: str, token: str) -> TokenInfo:
        if not ADDRESS_RE.match(token):
            raise ValueError("endereço de token fora do padrão")
        return parse_info(self._get(f"/networks/{self._net(net)}/tokens/{token}/info"))
