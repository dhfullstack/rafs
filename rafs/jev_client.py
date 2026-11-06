"""Cliente HTTP da API do Jev (iajev.com), conforme https://iajev.com/docs/api."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Callable

import httpx

log = logging.getLogger("rafs.jev")

BASE_URL = "https://iajev.com/api/v1"
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
MAX_STATE_FIELDS = 5
MAX_MESSAGE_CHARS = 8000
MAX_QUESTION_CHARS = 300
MAX_REQUEST_BYTES = 16000


class JevError(RuntimeError):
    pass


class JevAuthError(JevError):
    pass


class JevCreditsError(JevError):
    pass


class JevUnavailable(JevError):
    pass


class JevRateLimited(JevUnavailable):
    """429: insistir só gasta mais da cota da chave; quem chama deve esperar retry_after."""

    def __init__(self, detail: str, retry_after: float):
        super().__init__(detail)
        self.retry_after = retry_after


@dataclass(frozen=True)
class JevResult:
    decision: str | float
    confidence: float | None
    probabilities: dict[str, float]
    credits: float
    latency_ms: float | None
    request_id: str | None
    raw: dict


def validate_request(state: dict, decision: dict, idempotency_key: str) -> None:
    if not KEY_RE.match(idempotency_key):
        raise ValueError("idempotency_key inválida")
    message = state.get("message")
    if not isinstance(message, str) or not 1 <= len(message) <= MAX_MESSAGE_CHARS:
        raise ValueError("state.message obrigatório, até 8000 caracteres")
    extras = {k: v for k, v in state.items() if k != "message"}
    if len(extras) > MAX_STATE_FIELDS:
        raise ValueError("no máximo 5 campos extras em state")
    for k, v in extras.items():
        if not 1 <= len(k) <= 40:
            raise ValueError(f"nome de campo inválido: {k}")
        if isinstance(v, str) and len(v) > 300:
            raise ValueError(f"campo {k} com mais de 300 caracteres")
        if not isinstance(v, (str, int, float, bool)):
            raise ValueError(f"campo {k} precisa ser texto, número ou booleano")
    q = decision.get("question", "")
    if not 1 <= len(q) <= MAX_QUESTION_CHARS:
        raise ValueError("question precisa ter 1-300 caracteres")
    if decision.get("type") == "choice":
        choices = decision.get("choices") or []
        if not 2 <= len(choices) <= 8 or len(set(choices)) != len(choices) or any(not 1 <= len(c) <= 80 for c in choices):
            raise ValueError("choices: 2-8 strings únicas de até 80 caracteres")


def parse_response(body: dict, choices: list[str] | None) -> JevResult:
    if "decision" not in body or "results" not in body:
        raise JevError(f"resposta sem campos obrigatórios: {sorted(body)}")
    probs: dict[str, float] = {}
    for item in body["results"]:
        p = float(item["probability"])
        if not 0.0 <= p <= 1.0:
            raise JevError(f"probabilidade fora de [0,1]: {p}")
        probs[str(item["value"])] = p
    decision = body["decision"]
    if choices is not None and decision not in choices:
        raise JevError(f"decisão fora das opções: {decision!r}")
    conf = body.get("confidence")
    billing = body.get("billing") or {}
    meta = body.get("meta") or {}
    return JevResult(decision, None if conf is None else float(conf), probs, float(billing.get("credits", 0)),
                     meta.get("latencyMs"), meta.get("requestId"), body)


def normalize_api_keys(api_key: str | Sequence[str]) -> tuple[str, ...]:
    raw_items = api_key.split(",") if isinstance(api_key, str) else [
        part for item in api_key for part in str(item).split(",")
    ]
    keys: list[str] = []
    for raw in raw_items:
        clean = raw.strip().strip('"').strip("'")
        if clean and clean not in keys:
            keys.append(clean)
    return tuple(keys)


def _retry_after(r: httpx.Response) -> float:
    try:
        val = float(r.headers.get("Retry-After") or 60)
        return val if val > 0 else 60.0
    except ValueError:
        return 60.0


class JevClient:
    """Cliente HTTP do Jev com redundância instantânea entre múltiplas chaves em caso de HTTP 429."""

    def __init__(self, api_key: str | Sequence[str], base_url: str = BASE_URL, timeout: float = 20.0,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep,
                 clock: Callable[[], float] = time.time):
        self._keys = normalize_api_keys(api_key)
        if not self._keys:
            raise JevAuthError("JEV_API_KEY não configurada")
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        n = len(self._keys)
        self._active_idx = 0
        self._cooldown_until: list[float] = [0.0] * n
        self._strikes: list[int] = [0] * n
        self._disabled: list[str | None] = [None] * n
        self._last_429_detail = "HTTP 429"
        self._last_terminal_error: JevError | None = None

    @property
    def key_count(self) -> int:
        return len(self._keys)

    @property
    def active_slot(self) -> int:
        with self._lock:
            return self._active_idx + 1

    def close(self) -> None:
        self._http.close()

    def _pick_key(self, tried: set[int]) -> int | None:
        now = self._clock()
        n = len(self._keys)
        with self._lock:
            start = self._active_idx
            for offset in range(n):
                idx = (start + offset) % n
                if idx in tried or self._disabled[idx] is not None:
                    continue
                if self._cooldown_until[idx] <= now:
                    self._active_idx = idx
                    return idx
            if not tried:
                candidates = [i for i in range(n) if self._disabled[i] is None]
                if candidates:
                    idx = min(candidates, key=lambda i: self._cooldown_until[i])
                    self._active_idx = idx
                    return idx
            return None

    def _mark_ok(self, idx: int) -> None:
        with self._lock:
            self._strikes[idx] = 0
            self._cooldown_until[idx] = 0.0

    def _mark_rate_limited(self, idx: int, retry_after: float, detail: str) -> None:
        now = self._clock()
        with self._lock:
            self._strikes[idx] += 1
            self._cooldown_until[idx] = max(self._cooldown_until[idx], now + retry_after)
            self._last_429_detail = detail
            n = len(self._keys)
            next_idx = next(
                (cand for offset in range(1, n)
                 if self._disabled[(cand := (idx + offset) % n)] is None and self._cooldown_until[cand] <= now),
                None,
            )
            if next_idx is not None:
                if self._active_idx == idx:
                    self._active_idx = next_idx
                log.warning("Jev API #%d/%d retornou HTTP 429 (cooldown %.0fs) — mudando instantaneamente para API #%d/%d",
                            idx + 1, n, retry_after, next_idx + 1, n)
            else:
                if self._active_idx == idx:
                    self._active_idx = (idx + 1) % n
                if n > 1:
                    log.warning("Jev API #%d/%d retornou HTTP 429 (cooldown %.0fs) — sem outra API livre no momento",
                                idx + 1, n, retry_after)

    def _mark_disabled(self, idx: int, err: JevError) -> None:
        with self._lock:
            self._disabled[idx] = str(err)
            self._last_terminal_error = err
            n = len(self._keys)
            next_idx = next(
                (cand for offset in range(1, n) if self._disabled[(cand := (idx + offset) % n)] is None),
                None,
            )
            if next_idx is not None:
                if self._active_idx == idx:
                    self._active_idx = next_idx
                log.warning("Jev API #%d/%d indisponível (%s) — mudando instantaneamente para API #%d/%d",
                            idx + 1, n, err, next_idx + 1, n)

    def _handle_failover_status(self, idx: int, r: httpx.Response) -> JevError | None:
        detail = f"HTTP {r.status_code} {_error_code(r) or ''}".strip()
        if r.status_code == 429:
            retry_after = _retry_after(r)
            self._mark_rate_limited(idx, retry_after, detail)
            return JevRateLimited(detail, retry_after)
        if r.status_code in (401, 403):
            err: JevError = JevAuthError(detail)
            self._mark_disabled(idx, err)
            return err
        if r.status_code == 402:
            err = JevCreditsError(detail)
            self._mark_disabled(idx, err)
            return err
        return None

    def _exhausted_error(self, last_error: JevError | None) -> JevError:
        now = self._clock()
        with self._lock:
            n = len(self._keys)
            alive = [i for i in range(n) if self._disabled[i] is None]
            if alive:
                if n == 1 and isinstance(last_error, JevRateLimited):
                    wait = last_error.retry_after
                else:
                    waits = [self._cooldown_until[i] - now for i in alive if self._cooldown_until[i] > now]
                    wait = (min(waits) if waits
                            else (last_error.retry_after if isinstance(last_error, JevRateLimited) else 60.0))
                detail = last_error.args[0] if isinstance(last_error, JevRateLimited) else self._last_429_detail
                if n > 1:
                    log.warning("Jev: todas as %d API(s) ativas atingiram HTTP 429 (próxima libera em %.0fs)",
                                len(alive), wait)
                return JevRateLimited(detail, max(wait, 0.0))
            if isinstance(last_error, JevError):
                return last_error
            if self._last_terminal_error is not None:
                return self._last_terminal_error
            return JevUnavailable("nenhuma chave Jev disponível")

    def _get_json(self, path: str) -> dict:
        tried: set[int] = set()
        last_error: JevError | None = None
        while True:
            idx = self._pick_key(tried)
            if idx is None:
                raise self._exhausted_error(last_error)
            tried.add(idx)
            r = self._http.get(path, headers={"Authorization": f"Bearer {self._keys[idx]}"})
            if r.status_code == 200:
                self._mark_ok(idx)
                return r.json()
            err = self._handle_failover_status(idx, r)
            if err is not None:
                last_error = err
                continue
            self._raise_for(r)

    def credits(self) -> dict:
        return self._get_json("/credits")

    def models(self) -> dict:
        return self._get_json("/models")

    def run_choice(self, state: dict, question: str, choices: list[str], idempotency_key: str,
                   model: str = "jev") -> JevResult:
        decision = {"type": "choice", "question": question, "choices": choices}
        validate_request(state, decision, idempotency_key)
        payload = {"model": model, "state": state, "decision": decision}
        tried: set[int] = set()
        last_error: JevError | None = None
        while True:
            idx = self._pick_key(tried)
            if idx is None:
                raise self._exhausted_error(last_error)
            tried.add(idx)
            key = idempotency_key
            switched = False
            for attempt in range(4):
                try:
                    r = self._http.post("/decisions/run", json=payload,
                                        headers={"Authorization": f"Bearer {self._keys[idx]}",
                                                 "Idempotency-Key": key,
                                                 "Content-Type": "application/json"})
                except httpx.TransportError as e:
                    # resultado incerto: repetir com a MESMA chave
                    if attempt == 3:
                        raise JevUnavailable(f"falha de conexão: {e}") from e
                    self._sleep(2 * (attempt + 1))
                    continue
                if r.status_code == 200:
                    self._mark_ok(idx)
                    return parse_response(r.json(), choices)
                if r.status_code == 409:
                    code = _error_code(r)
                    if code == "REQUEST_PENDING":
                        self._sleep(2 * (attempt + 1))
                        continue
                    if code == "REQUEST_FAILED":
                        key = f"{idempotency_key[:120]}-r{attempt + 1}"  # falhas não são cobradas
                        continue
                err = self._handle_failover_status(idx, r)
                if err is not None:
                    last_error = err
                    switched = True
                    break
                self._raise_for(r)
            if not switched:
                raise JevUnavailable("tentativas esgotadas")

    @staticmethod
    def _raise_for(r: httpx.Response) -> None:
        if r.status_code < 400:
            return
        detail = f"HTTP {r.status_code} {_error_code(r) or ''}".strip()
        if r.status_code in (401, 403):
            raise JevAuthError(detail)
        if r.status_code == 402:
            raise JevCreditsError(detail)
        if r.status_code == 429:
            raise JevRateLimited(detail, _retry_after(r))
        if r.status_code >= 500:
            raise JevUnavailable(detail)
        raise JevError(detail)


def _error_code(r: httpx.Response) -> str | None:
    try:
        body = r.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return err.get("code")
        return body.get("code") or (err if isinstance(err, str) else None)
    return None
