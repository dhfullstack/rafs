"""Configuração lida de variáveis de ambiente (e opcionalmente de um arquivo .env)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_MODES = ("PAPER", "TESTNET", "FUTURES", "BACKTEST")
FUTURES_DEFAULT_SYMBOLS = "BTC/USDT,ETH/USDT,SOL/USDT"
JEV_KEY_SLOT_RE = re.compile(r"^JEV_API_KEY_(\d+)$")


class ConfigError(ValueError):
    pass


def load_dotenv(path: str | Path = ".env") -> None:
    """Carrega KEY=VALUE de um arquivo .env sem sobrescrever variáveis já definidas."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _get(env: dict[str, str], key: str, default: str) -> str:
    value = env.get(key, "")
    return value if value.strip() else default


def _flag(env: dict[str, str], key: str, default: str) -> bool:
    return _get(env, key, default).strip().lower() not in ("0", "false", "off", "nao", "não")


def parse_jev_api_keys(env: dict[str, str]) -> tuple[str, ...]:
    """Lê todas as chaves do Jev em ordem determinística (JEV_API_KEY_1..N, JEV_API_KEY e JEV_API_KEYS)."""
    numbered = sorted(
        ((int(m.group(1)), v) for k, v in env.items() if (m := JEV_KEY_SLOT_RE.match(k))),
        key=lambda item: item[0],
    )
    has_slot_1 = bool(env.get("JEV_API_KEY_1", "").strip())
    primary = env.get("JEV_API_KEY", "")
    raw_sources = ([*(v for _, v in numbered), primary, env.get("JEV_API_KEYS", "")] if has_slot_1
                   else [primary, *(v for _, v in numbered), env.get("JEV_API_KEYS", "")])
    keys: list[str] = []
    for raw in raw_sources:
        for part in raw.split(","):
            clean = part.strip().strip('"').strip("'")
            if clean and clean not in keys:
                keys.append(clean)
    return tuple(keys)


@dataclass(frozen=True)
class Settings:
    mode: str = "PAPER"
    exchange: str = "binance"
    symbols: tuple[str, ...] = ("BTC/BRL", "ETH/BRL", "SOL/BRL")  # pares do modo ativo
    paper_symbols: tuple[str, ...] = ()  # SYMBOLS
    testnet_symbols: tuple[str, ...] = ()  # TESTNET_SYMBOLS (vazio = usa SYMBOLS)
    futures_symbols: tuple[str, ...] = ()  # FUTURES_SYMBOLS (perpétuos USDT-M da demo)
    timeframe: str = "15m"
    poll_seconds: int = 30

    capital_brl: float = 200.0
    entry_brl: float = 15.0
    max_exposure_brl: float = 50.0
    daily_loss_limit_brl: float = 10.0
    max_positions_per_symbol: int = 1

    stop_atr_mult: float = 2.0
    take_profit_atr_mult: float = 3.0
    max_hold_candles: int = 32
    min_stop_pct: float = 0.0  # piso do stop em % (alvo escala junto); 0 = só ATR. Candle curto precisa de piso
    stop_cooldown_candles: int = 0  # depois de um stop, o book fica N candles sem entrar no mesmo ativo

    fee_rate: float = 0.001
    slippage_bps: float = 5.0

    jev_api_key: str = field(default="", repr=False)
    jev_api_keys: tuple[str, ...] = field(default=(), repr=False)
    jev_min_probability: float = 0.6
    jev_max_calls_per_day: int = 0  # 0 = ilimitado
    jev_trend_gate: bool = True  # só consulta o Jev para entrada com EMA12 > EMA26
    jev_prompt_version: str = "v2"  # v2: "alvo antes do stop?" com corte no ponto de equilíbrio
    jev_max_calls_per_hour: int = 0  # 0 = sem teto; a chave do Jev bloqueia (429) acima de ~10/h
    jev_edge_margin: float = 0.03  # v2: folga acima do equilíbrio exigida para comprar
    # trend = candidata no lado da EMA (compra com EMA12 > EMA26); fade = candidata contra o esticão da VWAP48
    # (compra ≥ fade_min_dev_pct abaixo, short ≥ fade_min_dev_pct acima). Backtest 60d 15m (SIMULADO): entrada
    # aleatória a favor da EMA ganhou 29%, no fade de 1% ganhou 40%.
    jev_entry_style: str = "trend"
    fade_min_dev_pct: float = 1.0

    xai_api_key: str = field(default="", repr=False)
    grok_model: str = "grok-4.20-0309-non-reasoning"
    grok_max_calls_per_day: int = 400  # 0 = ilimitado; ~US$ 0,001 por leitura no modelo padrão
    grok_min_score: float = 0.0  # score do Grok (chance estimada) a partir do qual vai ao Jev; 0 = só o escalate dele

    meme_sleeve: bool = True  # memes do guia em PAPER: GeckoTerminal -> Grok -> Jev -> Kelly × dial meme
    meme_bank_usd: float = 10_000.0  # banca SIMULADA de cada book de memes
    meme_nets: tuple[str, ...] = ("solana", "bsc")
    meme_take_profit_pct: float = 50.0
    meme_stop_pct: float = 25.0
    meme_max_hold_hours: int = 24
    meme_max_positions: int = 3
    meme_jev_max_calls_per_day: int = 60  # 2 por análise (escolha + chance); 0 = ilimitado
    meme_grok_max_calls_per_day: int = 120  # 1 por SCAN que chega ao CHIEF; 0 = ilimitado

    desk_rules: bool = True  # mesa do guia: dial e alvos do QUANT valem para todos os books (NO DIAL, NO DESK)
    quant_book: bool = True  # book "quant": rotação 12-1 × dial às 20:05 NY, o sleeve crypto do guia

    testnet_api_key: str = field(default="", repr=False)
    testnet_api_secret: str = field(default="", repr=False)
    futures_api_key: str = field(default="", repr=False)  # demo.binance.com, nunca a conta real
    futures_api_secret: str = field(default="", repr=False)

    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""
    discord_webhook_url: str = field(default="", repr=False)

    dashboard_password: str = field(default="", repr=False)
    dashboard_port: int = 8787

    data_dir: Path = Path("data")

    def __post_init__(self) -> None:
        sources: list[str] = []
        if self.jev_api_keys:
            sources.extend(str(x) for x in self.jev_api_keys)
        if self.jev_api_key:
            sources = [self.jev_api_key, *sources]
        keys: list[str] = []
        for raw in sources:
            for part in raw.split(","):
                clean = part.strip().strip('"').strip("'")
                if clean and clean not in keys:
                    keys.append(clean)
        final_keys = tuple(keys)
        object.__setattr__(self, "jev_api_keys", final_keys)
        object.__setattr__(self, "jev_api_key", final_keys[0] if final_keys else "")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = dict(os.environ) if env is None else env
        mode = _get(env, "MODE", "PAPER").upper()
        if mode == "LIVE":
            raise ConfigError(
                "MODE=LIVE não existe neste MVP. Execução real exige um adaptador "
                "auditado e autorização explícita do dono (ver docs/LIVE.md)."
            )
        if mode not in ALLOWED_MODES:
            raise ConfigError(f"MODE inválido: {mode}. Use um de {ALLOWED_MODES}.")
        def pairs(raw: str) -> tuple[str, ...]:
            return tuple(x.strip().upper() for x in raw.split(",") if x.strip())
        paper_symbols = pairs(_get(env, "SYMBOLS", "BTC/BRL,ETH/BRL,SOL/BRL"))
        testnet_symbols = pairs(env.get("TESTNET_SYMBOLS", ""))
        futures_symbols = pairs(_get(env, "FUTURES_SYMBOLS", FUTURES_DEFAULT_SYMBOLS))
        symbols = (futures_symbols if mode == "FUTURES" else
                   testnet_symbols if mode == "TESTNET" and testnet_symbols else paper_symbols)
        fut = mode == "FUTURES"

        def sized(key: str, default: str, fut_default: str) -> float:
            """FUTURES tem banca e taxa próprias (FUTURES_CAPITAL...): a demo vem com USDT e a taxa é outra."""
            return float(_get(env, "FUTURES_" + key.removesuffix("_BRL"), fut_default) if fut else _get(env, key, default))

        jev_keys = parse_jev_api_keys(env)
        s = cls(
            mode=mode,
            exchange=_get(env, "EXCHANGE", "binance").lower(),
            symbols=symbols,
            paper_symbols=paper_symbols,
            testnet_symbols=testnet_symbols,
            futures_symbols=futures_symbols,
            timeframe=_get(env, "TIMEFRAME", "15m"),
            poll_seconds=int(_get(env, "POLL_SECONDS", "30")),
            capital_brl=sized("CAPITAL_BRL", "200", "1000"),
            entry_brl=sized("ENTRY_BRL", "15", "100"),
            max_exposure_brl=sized("MAX_EXPOSURE_BRL", "50", "300"),
            daily_loss_limit_brl=sized("DAILY_LOSS_LIMIT_BRL", "10", "50"),
            max_positions_per_symbol=int(_get(env, "MAX_POSITIONS_PER_SYMBOL", "1")),
            stop_atr_mult=float(_get(env, "STOP_ATR_MULT", "2.0")),
            take_profit_atr_mult=float(_get(env, "TAKE_PROFIT_ATR_MULT", "3.0")),
            max_hold_candles=int(_get(env, "MAX_HOLD_CANDLES", "32")),
            min_stop_pct=float(_get(env, "MIN_STOP_PCT", "0")),
            stop_cooldown_candles=int(_get(env, "STOP_COOLDOWN_CANDLES", "0")),
            fee_rate=sized("FEE_RATE", "0.001", "0.0005"),
            slippage_bps=float(_get(env, "SLIPPAGE_BPS", "5")),
            jev_api_key=jev_keys[0] if jev_keys else "",
            jev_api_keys=jev_keys,
            jev_min_probability=float(_get(env, "JEV_MIN_PROBABILITY", "0.6")),
            jev_max_calls_per_day=int(_get(env, "JEV_MAX_CALLS_PER_DAY", "0")),
            jev_prompt_version=_get(env, "JEV_PROMPT_VERSION", "v2").strip().lower(),
            jev_edge_margin=float(_get(env, "JEV_EDGE_MARGIN", "0.03")),
            jev_max_calls_per_hour=int(_get(env, "JEV_MAX_CALLS_PER_HOUR", "0")),
            jev_trend_gate=_flag(env, "JEV_TREND_GATE", "1"),
            jev_entry_style=_get(env, "JEV_ENTRY_STYLE", "trend").strip().lower(),
            fade_min_dev_pct=float(_get(env, "FADE_MIN_DEV_PCT", "1.0")),
            xai_api_key=_get(env, "XAI_API_KEY", ""),
            grok_model=_get(env, "GROK_MODEL", "grok-4.20-0309-non-reasoning").strip(),
            grok_max_calls_per_day=int(_get(env, "GROK_MAX_CALLS_PER_DAY", "400")),
            grok_min_score=float(_get(env, "GROK_MIN_SCORE", "0")),
            meme_sleeve=_flag(env, "MEME_SLEEVE", "1"),
            meme_bank_usd=float(_get(env, "MEME_BANK_USD", "10000")),
            meme_nets=tuple(x.strip().lower() for x in _get(env, "MEME_NETS", "solana,bsc").split(",") if x.strip()),
            meme_take_profit_pct=float(_get(env, "MEME_TAKE_PROFIT_PCT", "50")),
            meme_stop_pct=float(_get(env, "MEME_STOP_PCT", "25")),
            meme_max_hold_hours=int(_get(env, "MEME_MAX_HOLD_HOURS", "24")),
            meme_max_positions=int(_get(env, "MEME_MAX_POSITIONS", "3")),
            meme_jev_max_calls_per_day=int(_get(env, "MEME_JEV_MAX_CALLS_PER_DAY", "60")),
            meme_grok_max_calls_per_day=int(_get(env, "MEME_GROK_MAX_CALLS_PER_DAY", "120")),
            desk_rules=_flag(env, "DESK_RULES", "1"),
            quant_book=_flag(env, "QUANT_BOOK", "1"),
            testnet_api_key=_get(env, "TESTNET_API_KEY", ""),
            testnet_api_secret=_get(env, "TESTNET_API_SECRET", ""),
            futures_api_key=_get(env, "FUTURES_API_KEY", ""),
            futures_api_secret=_get(env, "FUTURES_API_SECRET", ""),
            telegram_bot_token=_get(env, "TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=_get(env, "TELEGRAM_CHAT_ID", ""),
            discord_webhook_url=_get(env, "DISCORD_WEBHOOK_URL", ""),
            dashboard_password=_get(env, "DASHBOARD_PASSWORD", ""),
            dashboard_port=int(_get(env, "DASHBOARD_PORT", "8787")),
            data_dir=Path(_get(env, "DATA_DIR", "data")),
        )
        s.validate()
        return s

    @property
    def round_trip_cost_pct(self) -> float:
        return 2 * (self.fee_rate * 100 + self.slippage_bps / 100)

    @property
    def jev_pool_max_calls_per_hour(self) -> int:
        """Teto horário do conjunto de chaves (0 = sem teto). As chaves dividem a mesma cota da conta:
        em 30/10 as 11 travaram juntas (HTTP 429) depois de ~89 chamadas no dia, então somar não dá folga."""
        return self.jev_max_calls_per_hour

    @property
    def allow_short(self) -> bool:
        """Venda a descoberto só na demo de futuros; spot (PAPER/TESTNET) segue só comprando."""
        return self.mode == "FUTURES"

    def symbols_for(self, mode: str) -> tuple[str, ...]:
        """Pares de outro modo (o painel lê PAPER, TESTNET e FUTURES no mesmo processo)."""
        if mode == self.mode:
            return self.symbols
        if mode == "TESTNET" and self.testnet_symbols:
            return self.testnet_symbols
        if mode == "FUTURES":
            return self.futures_symbols or tuple(FUTURES_DEFAULT_SYMBOLS.split(","))
        return self.paper_symbols or self.symbols

    def validate(self) -> None:
        if self.mode not in ALLOWED_MODES:
            raise ConfigError(f"MODE inválido: {self.mode}. Use um de {ALLOWED_MODES}.")
        if not self.symbols:
            raise ConfigError("SYMBOLS vazio.")
        if self.entry_brl <= 0 or self.capital_brl <= 0:
            raise ConfigError("CAPITAL_BRL e ENTRY_BRL precisam ser positivos.")
        if self.entry_brl > self.max_exposure_brl:
            raise ConfigError("ENTRY_BRL não pode ser maior que MAX_EXPOSURE_BRL.")
        if self.max_exposure_brl > self.capital_brl:
            raise ConfigError("MAX_EXPOSURE_BRL não pode ser maior que CAPITAL_BRL.")
        if not 0.5 <= self.jev_min_probability <= 1.0:
            raise ConfigError("JEV_MIN_PROBABILITY deve estar entre 0.5 e 1.0.")
        if self.jev_prompt_version not in ("v1", "v2"):
            raise ConfigError("JEV_PROMPT_VERSION deve ser v1 ou v2.")
        if not 0.0 <= self.jev_edge_margin <= 0.3:
            raise ConfigError("JEV_EDGE_MARGIN deve estar entre 0 e 0.3.")
        if self.jev_entry_style not in ("trend", "fade"):
            raise ConfigError("JEV_ENTRY_STYLE deve ser trend ou fade.")
        if not 0.1 <= self.fade_min_dev_pct <= 10.0:
            raise ConfigError("FADE_MIN_DEV_PCT deve estar entre 0.1 e 10 (% de distância da VWAP48).")
        if self.jev_max_calls_per_hour < 0:
            raise ConfigError("JEV_MAX_CALLS_PER_HOUR não pode ser negativo (0 = sem teto).")
        if self.jev_max_calls_per_day < 0:
            raise ConfigError("JEV_MAX_CALLS_PER_DAY deve ser >= 0 (0 = ilimitado).")
        if self.grok_max_calls_per_day < 0:
            raise ConfigError("GROK_MAX_CALLS_PER_DAY deve ser >= 0 (0 = ilimitado).")
        if not 0.0 <= self.grok_min_score <= 1.0:
            raise ConfigError("GROK_MIN_SCORE deve estar entre 0 e 1 (0 = só o escalate do Grok).")
        if not 0.0 <= self.min_stop_pct <= 5.0:
            raise ConfigError("MIN_STOP_PCT deve estar entre 0 e 5 (% do preço; 0 = só ATR).")
        if not 0 <= self.stop_cooldown_candles <= 100:
            raise ConfigError("STOP_COOLDOWN_CANDLES deve estar entre 0 e 100.")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", self.grok_model):
            raise ConfigError("GROK_MODEL inválido.")
        if self.meme_sleeve:
            if not self.meme_nets or any(n not in ("solana", "bsc") for n in self.meme_nets):
                raise ConfigError("MEME_NETS aceita solana e/ou bsc.")
            if self.meme_bank_usd <= 0:
                raise ConfigError("MEME_BANK_USD precisa ser positivo.")
            if self.meme_take_profit_pct <= 0 or not 0 < self.meme_stop_pct < 100:
                raise ConfigError("MEME_TAKE_PROFIT_PCT > 0 e MEME_STOP_PCT entre 0 e 100.")
            if not 1 <= self.meme_max_hold_hours <= 168 or not 1 <= self.meme_max_positions <= 10:
                raise ConfigError("MEME_MAX_HOLD_HOURS 1-168 e MEME_MAX_POSITIONS 1-10.")
            if self.meme_jev_max_calls_per_day < 0 or self.meme_grok_max_calls_per_day < 0:
                raise ConfigError("MEME_JEV/GROK_MAX_CALLS_PER_DAY devem ser >= 0 (0 = ilimitado).")
        if self.mode == "TESTNET" and not (self.testnet_api_key and self.testnet_api_secret):
            raise ConfigError("MODE=TESTNET exige TESTNET_API_KEY e TESTNET_API_SECRET (testnet.binance.vision).")
        if self.mode == "FUTURES":
            if not (self.futures_api_key and self.futures_api_secret):
                raise ConfigError("MODE=FUTURES exige FUTURES_API_KEY e FUTURES_API_SECRET da conta DEMO "
                                  "(demo.binance.com → API Management).")
            if any(not s.endswith("/USDT") for s in self.symbols):
                raise ConfigError("FUTURES_SYMBOLS aceita só pares /USDT (perpétuos USDT-M).")
        if self.max_positions_per_symbol != 1:
            raise ConfigError("O MVP suporta apenas 1 posição por ativo.")

    @property
    def db_path(self) -> Path:
        # bancos separados: resultados de PAPER, TESTNET e FUTURES nunca se misturam
        return self.data_dir / {"TESTNET": "rafs-testnet.sqlite3",
                                "FUTURES": "rafs-futures.sqlite3"}.get(self.mode, "rafs.sqlite3")

    @property
    def kill_switch_path(self) -> Path:
        return self.data_dir / "KILL"
