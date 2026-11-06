"""Servidor do painel (stdlib, sem dependências). Login por senha, sessão em cookie HttpOnly, CSRF nos controles.

Controles expostos: pausar, retomar (exige digitar RESUME), ligar/desligar kill switch (desligar exige DISARM).
Não existe rota para mudar limites, chaves, corretora, sacar ou ligar LIVE.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import socket
import struct
import sys
import threading
import time
import zlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import Settings
from ..storage import Storage
from .state import build_state, db_for

log = logging.getLogger("rafs.dashboard")
STATIC = Path(__file__).parent / "static"
STATIC_FILES = {"app.js": "text/javascript", "app.css": "text/css", "manifest.webmanifest": "application/manifest+json",
                "rafs-art.png": "image/png"}
SESSION_TTL = 7 * 24 * 3600
LOGIN_WINDOW, LOGIN_MAX = 300, 5
MAX_BODY = 4096
CONFIRM = {"resume": "RESUME", "kill_off": "DISARM"}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def password_for(settings: Settings) -> tuple[str, bool]:
    """Senha do .env; se vazia, gera uma e guarda em data/ (fora do git). Retorna (senha, foi_gerada)."""
    if settings.dashboard_password:
        return settings.dashboard_password, False
    path = settings.data_dir / "dashboard_password.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip(), False
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    pw = secrets.token_urlsafe(9)
    path.write_text(pw, encoding="utf-8")
    return pw, True


def _png(size: int) -> bytes:
    """Ícone gerado em código (sem arquivo binário no repo): grade 3x3 de pontos em halftone, crescendo na diagonal."""
    bg, fg = (13, 13, 16), (169, 157, 255)
    # (centro_x, centro_y, raio) em fração do tamanho — mesmo desenho do logo SVG do painel
    dots = [((0.25 + 0.25 * c), (0.25 + 0.25 * r), 0.035 + 0.025 * (r + c)) for r in range(3) for c in range(3)]
    rows = []
    for y in range(size):
        row = bytearray([0])
        fy = (y + 0.5) / size
        for x in range(size):
            fx = (x + 0.5) / size
            px = bg
            for cx, cy, rad in dots:
                if (fx - cx) ** 2 + (fy - cy) ** 2 <= rad * rad:
                    px = fg
                    break
            row += bytes(px)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


class DashboardApp:
    """Estado do servidor separado do handler HTTP — testável sem abrir porta."""

    def __init__(self, settings: Settings, password: str, clock=time.time):
        self.s = settings
        self._password = password
        self.clock = clock
        self.sessions: dict[str, tuple[float, str]] = {}  # token -> (expira, csrf)
        self.attempts: dict[str, list[float]] = {}
        self.lock = threading.Lock()
        self._icons: dict[int, bytes] = {}

    # --- auth ---
    def login(self, ip: str, password: str) -> tuple[str, str] | None:
        now = self.clock()
        with self.lock:
            recent = [t for t in self.attempts.get(ip, []) if now - t < LOGIN_WINDOW]
            if len(recent) >= LOGIN_MAX:
                self.attempts[ip] = recent
                raise PermissionError("too many attempts; wait a few minutes")
            if not hmac.compare_digest(password.encode(), self._password.encode()):
                self.attempts[ip] = recent + [now]
                return None
            self.attempts.pop(ip, None)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            self.sessions[token] = (now + SESSION_TTL, csrf)
            return token, csrf

    def session(self, token: str | None) -> str | None:
        """Retorna o token CSRF da sessão válida, ou None."""
        if not token:
            return None
        with self.lock:
            entry = self.sessions.get(token)
            if entry is None:
                return None
            if entry[0] < self.clock():
                del self.sessions[token]
                return None
            return entry[1]

    def logout(self, token: str | None) -> None:
        with self.lock:
            self.sessions.pop(token or "", None)

    # --- dados ---
    def state(self, mode: str) -> dict:
        storage = Storage(db_for(self.s, mode))
        try:
            return build_state(storage, self.s, mode, self.s.kill_switch_path.exists())
        finally:
            storage.close()

    def control(self, mode: str, action: str, confirm: str, ip: str) -> str:
        if action in CONFIRM and confirm.strip().upper() != CONFIRM[action]:
            raise ValueError(f"wrong confirmation: type {CONFIRM[action]}")
        storage = Storage(db_for(self.s, mode))
        try:
            if action == "pause":
                storage.set_paused(True)
                msg = "New entries paused. Exits (stop/target) keep working."
            elif action == "resume":
                storage.set_paused(False)
                msg = "New entries allowed."
            elif action == "kill_on":
                self.s.data_dir.mkdir(parents=True, exist_ok=True)
                self.s.kill_switch_path.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
                msg = "Kill switch ARMED: no new entries in any environment."
            elif action == "kill_off":
                self.s.kill_switch_path.unlink(missing_ok=True)
                msg = "Kill switch disarmed."
            else:
                raise ValueError("unknown action")
            storage.log_event("CONTROL", f"{action} via painel ({ip}) [{mode}]")
            return msg
        finally:
            storage.close()

    def icon(self, size: int) -> bytes:
        if size not in self._icons:
            self._icons[size] = _png(size)
        return self._icons[size]


def make_handler(app: DashboardApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "rafs"
        sys_version = ""

        def log_message(self, fmt, *args):  # sem log de cada request (evita poluir o terminal do bot)
            pass

        # --- helpers ---
        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None, cache: bool = False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=300" if cache else "no-store")
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj, extra: dict | None = None):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", extra)

        def _token(self) -> str | None:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return c["gb_session"].value if "gb_session" in c else None

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("request too large")
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("invalid JSON")
            return data

        def _mode(self, value: str | None) -> str:
            return (value or app.s.mode).upper()

        # --- rotas ---
        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            if path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path.startswith("/static/") and path[8:] in STATIC_FILES:
                return self._send(200, (STATIC / path[8:]).read_bytes(), STATIC_FILES[path[8:]], cache=False)
            if path in ("/icon-180.png", "/icon-512.png"):
                return self._send(200, app.icon(int(path[6:9])), "image/png", cache=True)
            if path == "/api/session":
                csrf = app.session(self._token())
                return self._json(200, {"authenticated": csrf is not None, "csrf": csrf})
            if path == "/api/state":
                if app.session(self._token()) is None:
                    return self._json(401, {"error": "not authenticated"})
                try:
                    mode = self._mode(parse_qs(url.query).get("env", [None])[0])
                    return self._json(200, app.state(mode))
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                except Exception as e:  # noqa: BLE001
                    log.warning("painel /api/state: %s", e)
                    return self._json(500, {"error": f"{type(e).__name__}"})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            ip = self.client_address[0]
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, {"error": str(e)})
            if path == "/api/login":
                try:
                    res = app.login(ip, str(body.get("password", "")))
                except PermissionError as e:
                    return self._json(429, {"error": str(e)})
                if res is None:
                    return self._json(401, {"error": "wrong password"})
                token, csrf = res
                cookie = f"gb_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}"
                return self._json(200, {"csrf": csrf}, {"Set-Cookie": cookie})
            csrf = app.session(self._token())
            if csrf is None:
                return self._json(401, {"error": "not authenticated"})
            if not hmac.compare_digest(self.headers.get("X-CSRF", ""), csrf):
                return self._json(403, {"error": "invalid CSRF token"})
            if path == "/api/logout":
                app.logout(self._token())
                return self._json(200, {"ok": True}, {"Set-Cookie": "gb_session=; Max-Age=0; Path=/"})
            if path == "/api/control":
                try:
                    msg = app.control(self._mode(body.get("env")), str(body.get("action", "")),
                                      str(body.get("confirm", "")), ip)
                    return self._json(200, {"ok": True, "message": msg})
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
            return self._json(404, {"error": "not found"})

    return Handler


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))  # UDP não envia nada; só descobre a interface de saída
            return sk.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class DashboardServer(ThreadingHTTPServer):
    """No Windows, SO_REUSEADDR (padrão do http.server) deixa dois processos escutarem a mesma porta e as
    requisições caem em qualquer um. Exige a porta exclusiva: o segundo painel falha e o bot segue sem ele."""

    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def start(settings: Settings, lan: bool = False, background: bool = False) -> ThreadingHTTPServer | None:
    password, generated = password_for(settings)
    host = "0.0.0.0" if lan else "127.0.0.1"
    try:
        server = DashboardServer((host, settings.dashboard_port), make_handler(DashboardApp(settings, password)))
    except OSError:
        if not background:
            raise
        # outro processo (ex.: o bot PAPER) já abriu o painel; ele mostra os dois modos pelo seletor
        print(f"Painel: porta {settings.dashboard_port} já em uso — use o painel já aberto (seletor PAPER/TESTNET).")
        return None
    print(f"Painel: http://127.0.0.1:{settings.dashboard_port}")
    if lan:
        print(f"No celular (mesmo Wi-Fi): http://{lan_ip()}:{settings.dashboard_port}")
    if generated:
        print(f"Senha do painel (gerada agora, salva em {settings.data_dir / 'dashboard_password.txt'}): {password}")
    elif not settings.dashboard_password:
        print(f"Senha do painel: veja {settings.data_dir / 'dashboard_password.txt'}")
    if background:
        threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
    return server
