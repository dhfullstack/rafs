import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from http.server import ThreadingHTTPServer

import httpx
import pytest

from rafs.dashboard.server import DashboardApp, make_handler, password_for
from rafs.dashboard.state import build_state
from rafs.engine import FEATURE_WINDOW, Engine, FillRefs, run_paper
from rafs.exchanges.base import Quote
from rafs.exchanges.paper import PaperExchange
from rafs.notify import DiscordWebhook, fan_out
from rafs.risk import RiskEngine
from rafs.storage import Storage
from tests.conftest import make_candles, trend
from tests.test_engine import Scripted


def engine_with_position(settings, rules, telemetry=True):
    storage = Storage(settings.db_path)
    ex = PaperExchange(rules, settings.fee_rate, settings.slippage_bps)
    e = Engine(settings, storage, [Scripted()], ex, RiskEngine(settings, kill_switch_check=lambda: False),
               telemetry=telemetry)
    candles = make_candles(trend(FEATURE_WINDOW))
    e.process_candle("SOL/BRL", candles, FillRefs(candles[-1].close, candles[-1].close))
    return e, storage, candles


def test_state_reflects_books_positions_and_agents(settings, rules):
    e, storage, candles = engine_with_position(settings, rules)
    d = build_state(storage, settings, "PAPER", kill=False, now_ms=candles[-1].ts + 1000)
    json.dumps(d)  # serializável (sem inf/nan)
    book = d["books"][0]
    assert book["name"] == "scripted" and len(book["positions"]) == 1
    assert book["cash"] < settings.capital_brl and book["exposure"] > 0
    assert d["orders"][0]["status"] == "filled"
    assert d["decisions"][0]["action"] == "BUY"
    agents = {a["name"]: a for a in d["agents"]}
    assert set(agents) == {"CHIEF", "QUANT", "SCAN", "JUDGE", "STOCKS", "SIZE", "FILLS", "RISK"}
    assert agents["SCAN"]["activity"].startswith("SOL/BRL") and agents["FILLS"]["ok"]
    assert agents["JUDGE"]["status"] == "off" and agents["STOCKS"]["status"] == "off"  # sem Jev; sem Alpaca
    assert d["system"]["running"] is False  # sem heartbeat do loop
    assert "SIMULATED" in d["disclaimer"]


def test_backtest_engine_writes_no_telemetry(settings, rules):
    _, storage, _ = engine_with_position(settings, rules, telemetry=False)
    assert storage.get("agent:SCAN") is None


class FakeMD:
    def __init__(self, candles):
        self.candles = candles

    def fetch_closed_candles(self, symbol, tf, limit):
        return self.candles

    def get_quote(self, symbol):
        c = self.candles[-1].close
        return Quote(symbol, c * 0.999, c * 1.001, self.candles[-1].ts)


def test_run_loop_writes_heartbeat_quote_and_marks_running(settings, rules):
    storage = Storage(settings.db_path)
    e = Engine(settings, storage, [Scripted()], PaperExchange(rules, settings.fee_rate, settings.slippage_bps),
               RiskEngine(settings, kill_switch_check=lambda: False), telemetry=True)
    candles = make_candles(trend(FEATURE_WINDOW))
    run_paper(e, FakeMD(candles), max_iterations=1, sleep=lambda s: None)
    d = build_state(storage, settings, "PAPER", kill=False)
    assert d["system"]["running"] is True
    m = d["market"][0]
    assert m["bid"] and m["ask"] and m["rsi"] is not None
    agents = {a["name"]: a for a in d["agents"]}
    assert agents["CHIEF"]["status"] == "ok" and agents["SCAN"]["latency_ms"] is not None


def test_login_rate_limit_and_sessions(settings):
    now = [1000.0]
    app = DashboardApp(settings, "segredo", clock=lambda: now[0])
    assert app.login("1.2.3.4", "errada") is None
    for _ in range(4):
        app.login("1.2.3.4", "errada")
    with pytest.raises(PermissionError):
        app.login("1.2.3.4", "segredo")  # bloqueado mesmo com a senha certa
    now[0] += 301
    token, csrf = app.login("1.2.3.4", "segredo")
    assert app.session(token) == csrf
    now[0] += 8 * 24 * 3600
    assert app.session(token) is None  # expirou


def test_controls_require_confirmation(settings, rules):
    engine_with_position(settings, rules)
    app = DashboardApp(settings, "x")
    app.control("PAPER", "pause", "", "127.0.0.1")
    assert Storage(settings.db_path).is_paused()
    with pytest.raises(ValueError):
        app.control("PAPER", "resume", "sim", "127.0.0.1")
    app.control("PAPER", "resume", "resume", "127.0.0.1")
    assert not Storage(settings.db_path).is_paused()
    app.control("PAPER", "kill_on", "", "127.0.0.1")
    assert settings.kill_switch_path.exists()
    with pytest.raises(ValueError):
        app.control("PAPER", "kill_off", "", "127.0.0.1")
    app.control("PAPER", "kill_off", "DISARM", "127.0.0.1")
    assert not settings.kill_switch_path.exists()
    with pytest.raises(ValueError):
        app.control("PAPER", "set_limit", "", "127.0.0.1")
    with pytest.raises(ValueError):
        app.control("LIVE", "pause", "", "127.0.0.1")
    events = [m for _, lvl, m in Storage(settings.db_path).recent_events(10) if lvl == "CONTROL"]
    assert len(events) == 4


def test_password_generated_once_when_env_empty(settings):
    pw, generated = password_for(settings)
    assert generated and len(pw) >= 12
    assert password_for(settings) == (pw, False)
    assert password_for(replace(settings, dashboard_password="minha")) == ("minha", False)


@pytest.fixture
def http_server(settings, rules):
    engine_with_position(settings, rules)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(DashboardApp(settings, "segredo")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def req(url, method="GET", body=None, headers=None):
    r = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_http_auth_csrf_and_headers(http_server):
    code, headers, body = req(http_server + "/")
    assert code == 200 and b"rafs" in body
    assert "script-src 'self'" in headers["Content-Security-Policy"] and headers["X-Frame-Options"] == "DENY"
    assert req(http_server + "/api/state")[0] == 401
    assert req(http_server + "/api/login", "POST", {"password": "errada"})[0] == 401
    code, headers, body = req(http_server + "/api/login", "POST", {"password": "segredo"})
    assert code == 200 and "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    cookie = headers["Set-Cookie"].split(";")[0]
    csrf = json.loads(body)["csrf"]
    code, _, body = req(http_server + "/api/state?env=PAPER", headers={"Cookie": cookie})
    assert code == 200 and json.loads(body)["books"][0]["name"] == "scripted"
    assert req(http_server + "/api/control", "POST", {"action": "pause"}, {"Cookie": cookie})[0] == 403
    code, _, body = req(http_server + "/api/control", "POST", {"action": "pause"}, {"Cookie": cookie, "X-CSRF": csrf})
    assert code == 200 and json.loads(body)["ok"]
    assert req(http_server + "/static/../config.py")[0] == 404
    code, headers, body = req(http_server + "/icon-180.png")
    assert code == 200 and body.startswith(b"\x89PNG")


def test_discord_webhook_validates_and_posts():
    with pytest.raises(ValueError):
        DiscordWebhook("https://evil.example.com/hook")
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(204)
    hook = DiscordWebhook("https://discord.com/api/webhooks/1/abc", transport=httpx.MockTransport(handler))
    hook.send("🟢 compra")
    assert sent == [{"content": "🟢 compra", "allowed_mentions": {"parse": []}}]


def test_fan_out_survives_a_broken_channel():
    got = []

    def broken(text):
        raise RuntimeError("fora do ar")
    send = fan_out(broken, None, got.append)
    send("oi")
    assert got == ["oi"]
    assert fan_out(None, None) is None


def test_testnet_symbols_are_separate_from_paper(settings):
    from rafs.config import Settings
    keys = {"TESTNET_API_KEY": "k", "TESTNET_API_SECRET": "s"}
    env = {"MODE": "TESTNET", "SYMBOLS": "BTC/BRL", "TESTNET_SYMBOLS": "btc/usdt, eth/usdt", **keys}
    s = Settings.from_env(env)
    assert s.symbols == ("BTC/USDT", "ETH/USDT")
    assert s.symbols_for("PAPER") == ("BTC/BRL",)
    p = Settings.from_env({**env, "MODE": "PAPER"})
    assert p.symbols == ("BTC/BRL",) and p.symbols_for("TESTNET") == ("BTC/USDT", "ETH/USDT")
    assert Settings.from_env({"MODE": "TESTNET", "SYMBOLS": "BTC/BRL", **keys}).symbols == ("BTC/BRL",)


def test_state_reports_quote_currency_of_the_mode(settings, rules):
    _, storage, candles = engine_with_position(settings, rules)
    d = build_state(storage, replace(settings, symbols=("BTC/USDT",)), settings.mode, kill=False)
    assert d["quote"] == "USDT" and d["symbols"] == ["BTC/USDT"]


def test_cli_mode_flag_overrides_env(monkeypatch, tmp_path):
    import rafs.cli as cli
    seen = {}
    monkeypatch.setenv("MODE", "PAPER")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # TESTNET exige chaves; o teste não pode depender do .env de quem roda.
    monkeypatch.setenv("TESTNET_API_KEY", "test-key")
    monkeypatch.setenv("TESTNET_API_SECRET", "test-secret")
    monkeypatch.setattr(cli, "cmd_report", lambda s, a: seen.setdefault("mode", s.mode) and 0)
    cli.main(["report", "--mode", "testnet"])
    assert seen["mode"] == "TESTNET"


def test_second_dashboard_on_busy_port_does_not_crash_the_bot(settings):
    from rafs.dashboard.server import start
    first = start(replace(settings, dashboard_port=0, dashboard_password="x"), background=True)
    port = first.server_address[1]
    try:
        assert start(replace(settings, dashboard_port=port, dashboard_password="x"), background=True) is None
    finally:
        first.shutdown()
