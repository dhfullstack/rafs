"""Persistência em SQLite: decisões, ordens, posições, trades, estado e trilha de auditoria.

Tudo que é necessário para recuperar o estado após uma queda fica aqui.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from .exchanges.base import OrderResult
from .models import Decision, Position, Trade
from .portfolio import Book, utc_day

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (name TEXT PRIMARY KEY, cash REAL NOT NULL, initial REAL NOT NULL);
CREATE TABLE IF NOT EXISTS positions (book TEXT, symbol TEXT, data TEXT NOT NULL, PRIMARY KEY (book, symbol));
CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, book TEXT, symbol TEXT, exit_ts INTEGER, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders (client_order_id TEXT PRIMARY KEY, book TEXT, symbol TEXT, side TEXT,
    amount REAL, price REAL, fee REAL, status TEXT, note TEXT, simulated INTEGER, created_at INTEGER);
CREATE TABLE IF NOT EXISTS decisions (decider TEXT, symbol TEXT, candle_ts INTEGER, day TEXT, action TEXT,
    confidence REAL, reason TEXT, raw TEXT, credits REAL, created_at INTEGER, PRIMARY KEY (decider, symbol, candle_ts));
CREATE TABLE IF NOT EXISTS equity (book TEXT, ts INTEGER, equity REAL, PRIMARY KEY (book, ts));
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts INTEGER, level TEXT, message TEXT);
"""


class Storage:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10)
        if str(path) != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")  # painel lê enquanto o bot escreve
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # --- kv ---
    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))
        self.db.commit()

    def get_json(self, key: str, default=None):
        raw = self.get(key)
        return json.loads(raw) if raw else default

    def set_json(self, key: str, value) -> None:
        self.set(key, json.dumps(value))

    def set_agent(self, name: str, activity: str, ok: bool = True, latency_ms: float | None = None) -> None:
        """Telemetria por agente para o painel (último estado, não histórico)."""
        prev = self.get_json(f"agent:{name}", {}) or {}
        now = int(time.time() * 1000)
        self.set_json(f"agent:{name}", {
            "ts": now, "activity": activity[:300], "ok": ok,
            "latency_ms": round(latency_ms, 1) if latency_ms is not None else prev.get("latency_ms"),
            "last_error": prev.get("last_error") if ok else activity[:300],
            "last_error_ts": prev.get("last_error_ts") if ok else now,
            "runs": int(prev.get("runs", 0)) + 1,
        })

    def is_paused(self) -> bool:
        return self.get("paused", "0") == "1"

    def set_paused(self, paused: bool) -> None:
        self.set("paused", "1" if paused else "0")

    # --- auditoria ---
    def log_event(self, level: str, message: str) -> None:
        self.db.execute("INSERT INTO events(ts,level,message) VALUES(?,?,?)", (int(time.time() * 1000), level, message))
        self.db.commit()

    def recent_events(self, limit: int = 20) -> list[tuple[int, str, str]]:
        return self.db.execute("SELECT ts,level,message FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # --- books ---
    def load_book(self, name: str, initial: float) -> Book:
        row = self.db.execute("SELECT cash, initial FROM books WHERE name=?", (name,)).fetchone()
        if row is None:
            self.db.execute("INSERT INTO books(name,cash,initial) VALUES(?,?,?)", (name, initial, initial))
            self.db.commit()
            return Book(name, initial, initial)
        book = Book(name, row[0], row[1])
        for (data,) in self.db.execute("SELECT data FROM positions WHERE book=?", (name,)):
            p = Position(**json.loads(data))
            book.positions[p.symbol] = p
        for (data,) in self.db.execute("SELECT data FROM trades WHERE book=? ORDER BY id", (name,)):
            book.trades.append(Trade(**json.loads(data)))
        return book

    def book_names(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT name FROM books ORDER BY name")]

    def save_open(self, book: Book, pos: Position, order: OrderResult) -> None:
        """Abre posição + ordem + caixa numa única transação."""
        with self.db:
            self._insert_order(book.name, order)
            self.db.execute("INSERT INTO positions(book,symbol,data) VALUES(?,?,?)",
                            (book.name, pos.symbol, json.dumps(asdict(pos))))
            self.db.execute("UPDATE books SET cash=? WHERE name=?", (book.cash, book.name))

    def save_close(self, book: Book, trade: Trade, order: OrderResult) -> None:
        with self.db:
            self._insert_order(book.name, order)
            self.db.execute("DELETE FROM positions WHERE book=? AND symbol=?", (book.name, trade.symbol))
            self.db.execute("INSERT INTO trades(book,symbol,exit_ts,data) VALUES(?,?,?,?)",
                            (book.name, trade.symbol, trade.exit_ts, json.dumps(asdict(trade))))
            self.db.execute("UPDATE books SET cash=? WHERE name=?", (book.cash, book.name))

    def _insert_order(self, book: str, o: OrderResult) -> None:
        self.db.execute(
            "INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (o.client_order_id, book, o.symbol, o.side, o.amount, o.price, o.fee, o.status, o.note,
             int(o.simulated), int(time.time() * 1000)))

    def order_exists(self, client_order_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM orders WHERE client_order_id=?", (client_order_id,)).fetchone() is not None

    def record_rejected_order(self, book: str, o: OrderResult) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (o.client_order_id, book, o.symbol, o.side, o.amount, o.price, o.fee, o.status, o.note,
                             int(o.simulated), int(time.time() * 1000)))

    # --- decisões ---
    def get_decision(self, decider: str, symbol: str, candle_ts: int) -> tuple | None:
        return self.db.execute("SELECT action, reason FROM decisions WHERE decider=? AND symbol=? AND candle_ts=?",
                               (decider, symbol, candle_ts)).fetchone()

    def record_decision(self, d: Decision) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?)",
            (d.decider, d.symbol, d.candle_ts, utc_day(d.candle_ts), d.action.value, d.confidence, d.reason,
             json.dumps(d.raw), d.cost_credits, int(time.time() * 1000)))
        self.db.commit()

    def paid_calls_on(self, decider: str, day: str) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM decisions WHERE decider=? AND day=? AND credits>0",
                              (decider, day)).fetchone()
        return int(row[0])

    def paid_calls_total(self, decider: str) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM decisions WHERE decider=? AND credits>0", (decider,)).fetchone()
        return int(row[0])

    def requests_since(self, decider: str | tuple[str, ...], since_ms: int) -> int:
        """Requisições feitas à API (pagas ou recusadas), para o teto por hora. Vários deciders = mesma chave."""
        names = (decider,) if isinstance(decider, str) else tuple(decider)
        marks = ",".join("?" * len(names))
        row = self.db.execute(f"SELECT COUNT(*) FROM decisions WHERE decider IN ({marks}) AND created_at>=? "
                              "AND (credits>0 OR reason LIKE 'erro Jev:%')", (*names, since_ms)).fetchone()
        return int(row[0])

    def gated_count(self, decider: str) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM decisions WHERE decider=? AND reason LIKE 'filtro:%'",
                              (decider,)).fetchone()
        return int(row[0])

    def grok_screened_count(self, decider: str) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM decisions WHERE decider=? AND reason LIKE 'grok:%'",
                              (decider,)).fetchone()
        return int(row[0])

    def recent_trades(self, symbol: str, limit: int = 6) -> list[Trade]:
        """Últimas operações fechadas no ativo, de todos os books (contexto para o Grok)."""
        rows = self.db.execute("SELECT data FROM trades WHERE symbol=? ORDER BY exit_ts DESC, id DESC LIMIT ?",
                               (symbol, limit)).fetchall()
        return [Trade(**json.loads(d)) for (d,) in rows]

    def decisions_between(self, start_ms: int, end_ms: int) -> list[tuple[str, str, str, str, float]]:
        return self.db.execute("SELECT decider, action, reason, raw, credits FROM decisions "
                               "WHERE created_at>=? AND created_at<=?", (start_ms, end_ms)).fetchall()

    def events_between(self, start_ms: int, end_ms: int) -> list[tuple[int, str, str]]:
        return self.db.execute("SELECT ts, level, message FROM events WHERE ts>=? AND ts<=? ORDER BY id",
                               (start_ms, end_ms)).fetchall()

    def credits_spent(self, decider: str) -> float:
        row = self.db.execute("SELECT COALESCE(SUM(credits),0) FROM decisions WHERE decider=?", (decider,)).fetchone()
        return float(row[0])

    def decision_counts(self, decider: str) -> dict[str, int]:
        return {a: n for a, n in self.db.execute(
            "SELECT action, COUNT(*) FROM decisions WHERE decider=? GROUP BY action", (decider,))}

    # --- equity ---
    def record_equity(self, book: str, ts: int, equity: float) -> None:
        self.db.execute("INSERT OR REPLACE INTO equity VALUES(?,?,?)", (book, ts, equity))
        self.db.commit()

    def equity_curve(self, book: str) -> list[tuple[int, float]]:
        return self.db.execute("SELECT ts, equity FROM equity WHERE book=? ORDER BY ts", (book,)).fetchall()
