"""Linha de comando: python -m rafs <comando>."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from . import reports
from .backtest import cost_sensitivity, run_backtest, split_summary, summarize
from .config import ConfigError, Settings, load_dotenv
from .deciders.base import Decider
from .deciders.baseline import RandomBaseline, TrendBaseline
from .deciders.grok_screen import GrokScreener, Ledger
from .deciders.jev import JevDecider
from .deciders.rotation import QuantRotation
from .desk import MEME_BOOKS, Desk, desk_context, report_text
from .dial import compute_dial, fetch_inputs
from .fred import Fred
from .exchanges.ccxt_data import timeframe_ms
from .grok_client import GrokClient, GrokError
from .engine import Engine, run_paper
from .exchanges.ccxt_data import CCXTMarketData
from .exchanges.ccxt_futures import CCXTFuturesExchange, make_futures_exchange
from .exchanges.ccxt_testnet import CCXTTestnetExchange, make_testnet_exchange
from .exchanges.paper import PaperExchange
from .jev_client import JevClient, JevError
from .metrics import comparison_table
from .notify import DiscordWebhook, fan_out
from .storage import Storage
from .telegram import Telegram

log = logging.getLogger("rafs")


def setup_logging(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(settings.data_dir / "rafs.log", encoding="utf-8"),
    ])
    # httpx loga URLs completas — a do Telegram contém o token
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def jev_options(s: Settings, storage: Storage, live: bool = True) -> dict:
    return dict(trend_gate=s.jev_trend_gate, prompt_version=s.jev_prompt_version,
                max_calls_per_hour=s.jev_pool_max_calls_per_hour,
                recent_calls=lambda since_ms: storage.requests_since(("jev", "memes"), since_ms),  # pool de chaves
                cooldown_get=(lambda: float(storage.get("jev:cooldown_until", "0") or 0)) if live else None,
                cooldown_set=(lambda until: storage.set("jev:cooldown_until", repr(until))) if live else None,
                take_profit_atr_mult=s.take_profit_atr_mult, cost_pct=s.round_trip_cost_pct,
                edge_margin=s.jev_edge_margin, max_hold_candles=s.max_hold_candles, allow_short=s.allow_short,
                min_stop_pct=s.min_stop_pct, entry_style=s.jev_entry_style, fade_min_dev_pct=s.fade_min_dev_pct,
                screener=build_screener(s, storage) if live else None,  # backtest: sem Grok (custaria cada candle)
                context=(lambda: desk_context(storage)) if live else None)  # o Jev lê o dial da mesa


def build_screener(s: Settings, storage: Storage) -> GrokScreener | None:
    """CHIEF (Grok) lê tudo e escala ao Jev só o que importa. Sem XAI_API_KEY o Jev é consultado direto."""
    if not s.xai_api_key:
        log.warning("XAI_API_KEY vazia — sem leitura do Grok; o Jev será consultado direto (gasta mais a chave dele).")
        return None
    return GrokScreener(GrokClient(s.xai_api_key, s.grok_model), s.timeframe, timeframe_ms(s.timeframe),
                        Ledger(storage.get, storage.set), lambda sym: storage.recent_trades(sym),
                        max_calls_per_day=s.grok_max_calls_per_day, take_profit_atr_mult=s.take_profit_atr_mult,
                        stop_atr_mult=s.stop_atr_mult, cost_pct=s.round_trip_cost_pct,
                        max_hold_candles=s.max_hold_candles, context=lambda: desk_context(storage),
                        allow_short=s.allow_short, min_stop_pct=s.min_stop_pct, min_score=s.grok_min_score,
                        entry_style=s.jev_entry_style)


def build_deciders(s: Settings, storage: Storage, with_jev: bool, backtest_cap: int | None = None,
                   quant: bool = False, jev_client: JevClient | None = None) -> list[Decider]:
    """No backtest, `backtest_cap` limita o TOTAL de chamadas pagas ao Jev; no PAPER o limite é diário."""
    deciders: list[Decider] = [TrendBaseline(allow_short=s.allow_short), RandomBaseline(allow_short=s.allow_short)]
    if quant:
        deciders.append(QuantRotation())  # sleeve crypto do guia; só ao vivo (opera no relógio de NY)
    if with_jev:
        client = jev_client or (JevClient(s.jev_api_keys) if s.jev_api_keys else None)
        if client is None:
            log.warning("JEV_API_KEY vazia — decider Jev desligado.")
        elif backtest_cap is not None:
            deciders.insert(0, JevDecider(client, s.timeframe, s.stop_atr_mult,
                                          s.jev_min_probability, backtest_cap,
                                          lambda day: storage.paid_calls_total("jev"), key_prefix="gbj-b",
                                          **jev_options(s, storage, live=False)))
        else:
            deciders.insert(0, JevDecider(client, s.timeframe, s.stop_atr_mult,
                                          s.jev_min_probability, s.jev_max_calls_per_day,
                                          lambda day: storage.paid_calls_on("jev", day),
                                          key_prefix={"TESTNET": "gbj-t", "FUTURES": "gbj-f"}.get(s.mode, "gbj-p"),
                                          **jev_options(s, storage)))
    return deciders


def market_data(s: Settings) -> CCXTMarketData:
    """Candles e cotação de onde as ordens do modo executam: demo de futuros, testnet spot ou Binance pública."""
    if s.mode == "FUTURES":
        return CCXTMarketData(s.exchange, demo_futures=True, real_candles=True)
    return CCXTMarketData(s.exchange, sandbox=s.mode == "TESTNET")


def build_exchange(s: Settings):
    """PAPER: execução simulada local. TESTNET: ordens reais na testnet (dinheiro fictício), dados da testnet.
    FUTURES: long e short a 1x na conta DEMO de futuros USDT-M (dinheiro fictício), dados da demo."""
    if s.mode == "FUTURES":
        return market_data(s), CCXTFuturesExchange(make_futures_exchange(s.futures_api_key, s.futures_api_secret),
                                                   s.fee_rate)
    if s.mode == "TESTNET":
        md = CCXTMarketData(s.exchange, sandbox=True)
        return md, CCXTTestnetExchange(make_testnet_exchange(s.exchange, s.testnet_api_key, s.testnet_api_secret),
                                       s.fee_rate)
    md = CCXTMarketData(s.exchange)
    return md, PaperExchange(md.get_trading_rules, s.fee_rate, s.slippage_bps)


def build_desk(s: Settings, storage: Storage, engine: Engine, md, exchange, gecko=None,
               jev_client: JevClient | None = None) -> Desk:
    """A mesa do guia: CHIEF prova as ferramentas, QUANT publica o dial, RISK da conta, relatório das 23:55."""
    fred = Fred()
    # dial usa candles diários reais, não os da testnet/demo
    public = CCXTMarketData(s.exchange) if s.mode in ("TESTNET", "FUTURES") else md

    def publish(ny):
        fred_data, daily, errors = fetch_inputs(fred, public)
        if not fred_data and not daily:
            raise RuntimeError("todas as fontes do dial falharam: " + "; ".join(errors))
        d = compute_dial(fred_data, daily, ny)
        d["errors"] = errors
        return d

    tools = {}
    if s.xai_api_key:
        def grok():
            ids = GrokClient(s.xai_api_key, s.grok_model).models()
            if s.grok_model not in ids:
                raise RuntimeError(f"{s.grok_model} não está na conta")
            return f"{s.grok_model} disponível"
        tools["Grok"] = grok
    if "jev" in engine.books or (gecko is not None and s.jev_api_keys):
        client = jev_client or JevClient(s.jev_api_keys)
        tools["Jev"] = lambda: f"{len(client.models())} entradas em /models ({client.key_count} API(s))"
    if gecko is not None:
        tools["GeckoTerminal"] = lambda: f"{len(gecko.trending(s.meme_nets[0], '1h'))} pools em alta ({s.meme_nets[0]})"
    tools["FRED"] = lambda: f"VIXCLS até {fred.series('VIXCLS')[-1][0]}"
    tools["Binance dados"] = lambda: f"BTC/USDT 1d fechou {public.fetch_closed_candles('BTC/USDT', '1d', 3)[-1].close:g}"
    if isinstance(exchange, CCXTTestnetExchange):
        tools["Corretora"] = lambda: f"testnet USDT livre {exchange.balances().get('USDT', 0):g}"
    elif isinstance(exchange, CCXTFuturesExchange):
        tools["Corretora"] = lambda: f"demo futuros USDT livre {exchange.balances().get('USDT', 0):g}"
    else:
        tools["Corretora"] = lambda: "execução simulada local"
    need = {"jev": ("Jev", "Grok", "Corretora"), "baseline": ("Corretora",), "random": ("Corretora",),
            "quant": ("FRED", "Binance dados", "Corretora"),
            "memes": ("GeckoTerminal", "Grok", "Jev"), "memes_top": ("GeckoTerminal",)}
    books = list(engine.books) + (list(MEME_BOOKS) if gecko is not None else [])
    sleeves = {b: tuple(t for t in need.get(b, ("Corretora",)) if t in tools) for b in books}
    return Desk(s, storage, engine, public, publish, tools, sleeves, gate_all_books=s.desk_rules)


def cmd_run(s: Settings, args) -> int:
    storage = Storage(s.db_path)
    md, exchange = build_exchange(s)
    for sym in s.symbols:
        exchange.get_trading_rules(sym)  # falha cedo se o par não existir
    if isinstance(exchange, CCXTFuturesExchange):
        for note in exchange.prepare(s.symbols):  # one-way, isolada, 1x (sem 1x o bot nem sobe)
            log.warning("FUTURES: %s", note)
            storage.log_event("RECONCILE", f"FUTURES: {note}")
    telegram = None
    notify = None
    if s.telegram_bot_token and s.telegram_chat_id:
        holder: dict = {}
        telegram = Telegram(
            s.telegram_bot_token, s.telegram_chat_id,
            handlers={
                "/status": lambda: reports.status_text(storage, s, holder["e"].last_prices, holder["e"].risk.kill_switch_active()),
                "/balance": lambda: reports.balance_text(storage, s.capital_brl, holder["e"].last_prices),
                "/positions": lambda: reports.positions_text(storage, s.capital_brl, holder["e"].last_prices),
                "/performance": lambda: reports.performance_text(storage, s.capital_brl),
            },
            on_pause=lambda: (storage.set_paused(True), storage.log_event("CONTROL", "pausado via Telegram")),
            get_offset=lambda: int(storage.get("tg_offset", "0")),
            set_offset=lambda v: storage.set("tg_offset", str(v)),
        )
        notify = telegram.send
    discord = DiscordWebhook(s.discord_webhook_url) if s.discord_webhook_url else None
    notify = fan_out(notify, discord.send if discord else None)
    jev_client = JevClient(s.jev_api_keys) if s.jev_api_keys and not args.no_jev else None
    if jev_client is not None:
        storage.set("jev:cooldown_until", "0")  # limpa pausa antiga ao subir com o pool de APIs
        log.info("Jev: %d API(s) configurada(s) em redundância (failover instantâneo em HTTP 429).",
                 jev_client.key_count)
    engine = Engine(s, storage,
                    build_deciders(s, storage, with_jev=not args.no_jev, quant=s.quant_book, jev_client=jev_client),
                    exchange, notify=notify, telemetry=True)
    gecko = None
    if s.meme_sleeve:
        from .gecko import GeckoTerminal
        gecko = GeckoTerminal()  # um cliente só: SCAN e checagem do CHIEF dividem a cota grátis
    meme_jev_client = jev_client or (JevClient(s.jev_api_keys) if s.jev_api_keys else None)
    desk = build_desk(s, storage, engine, md, exchange, gecko, jev_client=meme_jev_client)
    if gecko is not None:
        from .memes import start_thread
        start_thread(s, gecko, notify, jev_client=meme_jev_client)  # PAPER: SCAN 15 min, RISK 5 min, banca SIMULADA
    if telegram is not None:
        holder["e"] = engine
    log.info("Modo %s | execução: %s | deciders: %s", s.mode, exchange.get_capabilities().name, list(engine.books))
    if isinstance(exchange, (CCXTTestnetExchange, CCXTFuturesExchange)):
        for issue in exchange.reconcile(engine.books.values()):
            log.warning("RECONCILIAÇÃO: %s", issue)
            storage.log_event("RECONCILE", issue)
            engine.notify(f"⚠️ Reconciliação: {issue}")
    if args.dashboard:
        from .dashboard.server import start
        start(s, lan=args.lan, background=True)
    try:
        run_paper(engine, md, telegram, max_iterations=args.iterations, desk=desk)
    except KeyboardInterrupt:
        log.info("Interrompido pelo usuário.")
    return 0


def cmd_backtest(s: Settings, args) -> int:
    md = CCXTMarketData(s.exchange)
    now = md.exchange.milliseconds()
    since = now - args.days * 86_400_000
    histories = {}
    for sym in s.symbols:
        log.info("Baixando histórico %s (%d dias)...", sym, args.days)
        histories[sym] = md.fetch_history(sym, s.timeframe, since, now)
        log.info("  %d candles", len(histories[sym]))
    log.info("Simulando...")
    log.setLevel(logging.WARNING)  # sem log por trade no backtest
    memory = Storage(":memory:")
    deciders = build_deciders(s, memory, with_jev=args.with_jev, backtest_cap=args.jev_max_calls)
    engine = run_backtest(s, deciders, histories, md.get_trading_rules, storage=memory)
    print(f"\nBACKTEST {args.days}d {s.timeframe} {', '.join(s.symbols)} — taxa {s.fee_rate * 100:.2f}%/lado, "
          f"slippage {s.slippage_bps} bps (resultado SIMULADO)\n")
    print(comparison_table(summarize(engine)))
    split_ts = since + int((now - since) * 0.7)
    first, second = split_summary(engine, split_ts)
    print("\nPrimeiros 70% do período:")
    print(comparison_table(first))
    print("\nÚltimos 30% do período:")
    print(comparison_table(second))
    if args.sensitivity:
        print("\nSensibilidade a slippage (bps):")
        sens = cost_sensitivity(s, lambda: build_deciders(s, memory, with_jev=False), histories, md.get_trading_rules)
        for bps, rows in sens.items():
            print(f"\n-- {bps} bps --")
            print(comparison_table(rows))
    return 0


def cmd_report(s: Settings, args) -> int:
    storage = Storage(s.db_path)
    prices = {}
    try:
        md = market_data(s)
        for sym in s.symbols:
            prices[sym] = md.get_quote(sym).mid
    except Exception as e:  # noqa: BLE001
        print(f"(sem preços atuais: {e})")
    print(reports.status_text(storage, s, prices, s.kill_switch_path.exists()))
    print()
    print(reports.balance_text(storage, s.capital_brl, prices))
    print()
    print(reports.positions_text(storage, s.capital_brl, prices))
    print()
    print(reports.performance_text(storage, s.capital_brl))
    print("\nÚltimos eventos:")
    for ts, level, msg in storage.recent_events(10):
        print(f"  {time.strftime('%m-%d %H:%M', time.localtime(ts / 1000))} {level}: {msg}")
    return 0


def cmd_dial(s: Settings, args) -> int:
    """QUANT agora: calcula o dial com os dados de hoje e imprime (não grava, não opera)."""
    from datetime import datetime
    from .dial import NY
    fred_data, daily, errors = fetch_inputs(Fred(), CCXTMarketData(s.exchange))
    d = compute_dial(fred_data, daily, datetime.now(NY))
    d["errors"] = errors
    print(json.dumps(d, indent=2, ensure_ascii=False))
    return 0


def cmd_nightshift(s: Settings, args) -> int:
    from . import nightshift
    md = CCXTMarketData(s.exchange)
    print("Baixando FRED e candles diários da Binance (leitura pública)...")
    fred_data, daily = nightshift.fetch(Fred(), md, md.exchange.milliseconds(), years=args.years)
    print(nightshift.run(fred_data, daily))
    return 0


def cmd_desk_report(s: Settings, args) -> int:
    from .desk import ny_day
    storage = Storage(s.db_path)
    prices = {}
    try:
        md = market_data(s)
        prices = {sym: md.get_quote(sym).mid for sym in s.symbols}
    except Exception as e:  # noqa: BLE001
        print(f"(sem preços atuais: {e})")
    now = int(time.time() * 1000)
    print(report_text(storage, s, prices, ny_day(now), now))
    return 0


def cmd_check(s: Settings, args) -> int:
    ok = True
    print(f"Modo: {s.mode} | banco: {s.db_path}")
    try:
        vix = Fred().series("VIXCLS")[-1]
        print(f"[ok] FRED: VIXCLS {vix[1]:g} em {vix[0]} (dial do QUANT)")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[FALHA] FRED: {type(e).__name__}: {e} (sem FRED o dial sai DEGRADED)")
    md = market_data(s)
    if s.mode == "FUTURES":
        try:
            _, ex = build_exchange(s)
            hosts = sorted({u.split("/")[2] for u in ex.ex.urls["api"].values()})
            free = ex.balances().get("USDT", 0.0)
            net = ex.net_positions()
            print(f"[ok] Demo futuros {', '.join(hosts)}: USDT livre {free:g}; posições líquidas "
                  + (", ".join(f"{k} {v:+g}" for k, v in net.items()) or "nenhuma"))
            if free < s.max_exposure_brl:
                ok = False
                print(f"[FALHA] USDT livre na demo ({free:g}) menor que FUTURES_MAX_EXPOSURE ({s.max_exposure_brl:g})")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[FALHA] Demo futuros: {type(e).__name__}: {e}")
    if s.mode == "TESTNET":
        try:
            _, ex = build_exchange(s)
            bal = ex.balances()
            quotes = sorted({sym.split("/")[1] for sym in s.symbols} | {sym.split("/")[0] for sym in s.symbols})
            print(f"[ok] Testnet {ex.ex.urls['api']['private']}: saldo livre " +
                  ", ".join(f"{c} {bal.get(c, 0):g}" for c in quotes))
            for sym in s.symbols:
                quote = sym.split("/")[1]
                if bal.get(quote, 0) < s.max_exposure_brl:
                    ok = False
                    print(f"[FALHA] saldo {quote} na testnet ({bal.get(quote, 0):g}) menor que MAX_EXPOSURE_BRL — "
                          f"use pares com outra moeda de cotação (ex.: SYMBOLS=BTC/USDT,...)")
                    break
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[FALHA] Testnet: {type(e).__name__}: {e}")
    for sym in s.symbols:
        try:
            r = md.get_trading_rules(sym)
            c = md.fetch_closed_candles(sym, s.timeframe, 120)
            q = md.get_quote(sym)
            print(f"[ok] {sym}: {len(c)} candles, bid {q.bid} ask {q.ask} spread {q.spread_pct:.3f}% "
                  f"mínimo {sym.split("/")[1]} {r.min_cost} passo {r.amount_step}")
            if s.entry_brl < r.min_cost * 1.2:
                print(f"     aviso: ENTRY_BRL={s.entry_brl} muito perto do mínimo; arredondamento pode rejeitar ordens")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[FALHA] {sym}: {e}")
    if s.jev_api_keys:
        ok_keys = 0
        total_keys = len(s.jev_api_keys)
        for idx, key in enumerate(s.jev_api_keys, 1):
            client = JevClient(key)
            try:
                print(f"[ok] Jev API #{idx}/{total_keys}: créditos {client.credits()}")
                ok_keys += 1
            except JevError as e:
                print(f"[FALHA] Jev API #{idx}/{total_keys}: {e}")
            finally:
                client.close()
        if ok_keys == 0:
            ok = False
        elif total_keys > 1:
            print(f"[ok] Jev redundância: {ok_keys}/{total_keys} APIs operacionais")
    else:
        print("[--] Jev: JEV_API_KEY não configurada (decider Jev ficará desligado)")
    if s.xai_api_key:
        try:
            ids = GrokClient(s.xai_api_key, s.grok_model).models()
            found = s.grok_model in ids
            ok &= found
            print(f"[{'ok' if found else 'FALHA'}] Grok: {len(ids)} modelos na conta; "
                  f"{s.grok_model} {'disponível' if found else 'NÃO encontrado (ajuste GROK_MODEL)'}")
        except GrokError as e:
            ok = False
            print(f"[FALHA] Grok: {e}")
    else:
        print("[--] Grok: XAI_API_KEY não configurada (Jev consultado direto, sem leitura do Grok)")
    if s.telegram_bot_token and s.telegram_chat_id:
        import httpx
        try:
            r = httpx.get(f"https://api.telegram.org/bot{s.telegram_bot_token}/getMe", timeout=10)
            print(f"[{'ok' if r.status_code == 200 else 'FALHA'}] Telegram: HTTP {r.status_code}")
            ok &= r.status_code == 200
        except httpx.HTTPError as e:
            ok = False
            print(f"[FALHA] Telegram: {type(e).__name__}")
    else:
        print("[--] Telegram: não configurado")
    if s.discord_webhook_url:
        try:
            print(f"[ok] Discord: webhook '{DiscordWebhook(s.discord_webhook_url).check()}'")
        except Exception as e:  # noqa: BLE001 — não imprime a URL (contém o segredo do webhook)
            ok = False
            print(f"[FALHA] Discord: {type(e).__name__}")
    else:
        print("[--] Discord: não configurado")
    print(f"Kill switch: {'ATIVO' if s.kill_switch_path.exists() else 'off'}")
    return 0 if ok else 1


def cmd_dashboard(s: Settings, args) -> int:
    from .dashboard.server import start
    server = start(s, lan=args.lan)
    print("Ctrl+C para parar o painel (o bot, se estiver rodando em outro terminal, continua).")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_pause(s: Settings, args) -> int:
    st = Storage(s.db_path)
    st.set_paused(True)
    st.log_event("CONTROL", "pausado via CLI")
    print("Novas entradas pausadas.")
    return 0


def cmd_resume(s: Settings, args) -> int:
    if not args.yes and input("Digite RETOMAR para liberar novas entradas (PAPER): ").strip() != "RETOMAR":
        print("Cancelado.")
        return 1
    st = Storage(s.db_path)
    st.set_paused(False)
    st.log_event("CONTROL", "retomado via CLI")
    print("Entradas liberadas.")
    return 0


def cmd_kill(s: Settings, args) -> int:
    s.data_dir.mkdir(parents=True, exist_ok=True)
    if args.off:
        s.kill_switch_path.unlink(missing_ok=True)
        print("Kill switch desativado.")
    else:
        s.kill_switch_path.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        print(f"Kill switch ATIVO ({s.kill_switch_path}). Novas entradas bloqueadas; stops seguem ativos.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rafs", description="rafs — MVP PAPER")
    sub = p.add_subparsers(dest="cmd", required=True)
    mode = argparse.ArgumentParser(add_help=False)
    mode.add_argument("--mode", type=str.upper, choices=["PAPER", "TESTNET", "FUTURES"],
                      help="sobrescreve MODE do .env (FUTURES = long e short na demo de futuros, dinheiro fictício)")
    r = sub.add_parser("run", help="roda o paper trading ao vivo", parents=[mode])
    r.add_argument("--no-jev", action="store_true", help="roda só baseline e aleatório")
    r.add_argument("--iterations", type=int, default=None, help="para depois de N ciclos (teste)")
    r.add_argument("--dashboard", action="store_true", help="abre o painel web junto com o bot")
    r.add_argument("--lan", action="store_true", help="painel acessível pelo celular na mesma rede Wi-Fi")
    dsh = sub.add_parser("dashboard", help="painel web (lê o banco; o bot pode rodar em outro terminal)", parents=[mode])
    dsh.add_argument("--lan", action="store_true", help="acessível pelo celular na mesma rede Wi-Fi")
    b = sub.add_parser("backtest", help="backtest histórico")
    b.add_argument("--days", type=int, default=30)
    b.add_argument("--with-jev", action="store_true", help="inclui o Jev (GASTA créditos)")
    b.add_argument("--jev-max-calls", type=int, default=50)
    b.add_argument("--sensitivity", action="store_true")
    sub.add_parser("report", help="status, saldo, posições e performance", parents=[mode])
    sub.add_parser("check", help="verifica conexões e configuração", parents=[mode])
    sub.add_parser("pause", help="pausa novas entradas", parents=[mode])
    rs = sub.add_parser("resume", help="libera novas entradas (pede confirmação)", parents=[mode])
    rs.add_argument("--yes", action="store_true")
    sub.add_parser("dial", help="QUANT: calcula o dial de hoje e imprime (não grava)")
    ns = sub.add_parser("nightshift", help="testa o dial e a regra crypto do guia no histórico (não gasta nada)")
    ns.add_argument("--years", type=int, default=8)
    sub.add_parser("desk-report", help="relatório da mesa de hoje (o mesmo das 23:55 NY)", parents=[mode])
    k = sub.add_parser("kill", help="ativa o kill switch")
    k.add_argument("--off", action="store_true", help="desativa o kill switch")
    args = p.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    if getattr(args, "mode", None):
        os.environ["MODE"] = args.mode
    try:
        s = Settings.from_env()
    except ConfigError as e:
        print(f"Configuração inválida: {e}", file=sys.stderr)
        return 2
    setup_logging(s)
    handlers = {"run": cmd_run, "backtest": cmd_backtest, "report": cmd_report, "check": cmd_check,
                "pause": cmd_pause, "dashboard": cmd_dashboard, "resume": cmd_resume, "kill": cmd_kill,
                "dial": cmd_dial, "nightshift": cmd_nightshift, "desk-report": cmd_desk_report}
    return handlers[args.cmd](s, args)
