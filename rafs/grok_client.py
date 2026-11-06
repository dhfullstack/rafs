"""Cliente HTTP do Grok (xAI), conforme https://docs.x.ai (chat/completions com saída JSON estruturada)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.20-0309-non-reasoning"
# US$ por milhão de tokens (tabela de docs.x.ai/docs/models, faixa < 200k), usado só se a API não informar o custo
PRICES_PER_MTOK = {
    "grok-4.20-0309-non-reasoning": (1.25, 2.50),
    "grok-4.20-0309-reasoning": (1.25, 2.50),
    "grok-4.3": (1.25, 2.50),
    "grok-4.7": (2.00, 6.00),
    "grok-build-0.1": (1.00, 2.00),
}
USD_TICKS = 10_000_000_000  # docs.x.ai: cost_in_usd_ticks, 1e10 ticks = US$ 1


class GrokError(RuntimeError):
    pass


class GrokAuthError(GrokError):
    pass


class GrokUnavailable(GrokError):
    pass


class GrokRateLimited(GrokUnavailable):
    def __init__(self, detail: str, retry_after: float):
        super().__init__(detail)
        self.retry_after = retry_after


@dataclass(frozen=True)
class GrokResult:
    data: dict
    cost_usd: float
    latency_ms: float
    model: str


def usage_cost_usd(usage: dict, model: str) -> float:
    ticks = usage.get("cost_in_usd_ticks")
    if isinstance(ticks, (int, float)) and ticks >= 0:
        return ticks / USD_TICKS
    p_in, p_out = PRICES_PER_MTOK.get(model, (2.00, 6.00))  # modelo desconhecido: assume o mais caro da tabela
    return (usage.get("prompt_tokens", 0) * p_in + usage.get("completion_tokens", 0) * p_out) / 1_000_000


class GrokClient:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, base_url: str = BASE_URL, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise GrokAuthError("XAI_API_KEY não configurada")
        self.model = model
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def close(self) -> None:
        self._http.close()

    def models(self) -> list[str]:
        r = self._http.get("/models")
        _raise_for(r)
        return sorted(m.get("id", "") for m in r.json().get("data", []))

    def structured(self, system: str, user: str, schema_name: str, schema: dict) -> GrokResult:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
        }
        t0 = time.perf_counter()
        try:
            r = self._http.post("/chat/completions", json=payload)
        except httpx.TransportError as e:
            raise GrokUnavailable(f"falha de conexão: {type(e).__name__}") from e
        _raise_for(r)
        body = r.json()
        try:
            content = body["choices"][0]["message"]["content"]
            data = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise GrokError(f"resposta fora do formato: {type(e).__name__}") from e
        if not isinstance(data, dict):
            raise GrokError("resposta não é um objeto JSON")
        return GrokResult(data, usage_cost_usd(body.get("usage") or {}, self.model),
                          (time.perf_counter() - t0) * 1000, body.get("model", self.model))


def _raise_for(r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    detail = f"HTTP {r.status_code}"
    try:
        err = r.json().get("error")
        code = err.get("code") if isinstance(err, dict) else err
        if code:
            detail += f" {str(code)[:80]}"
    except (ValueError, AttributeError):
        pass
    if r.status_code in (401, 403):
        raise GrokAuthError(detail)
    if r.status_code == 429:
        try:
            retry_after = float(r.headers.get("Retry-After") or 60)
        except ValueError:
            retry_after = 60.0
        raise GrokRateLimited(detail, retry_after)
    if r.status_code >= 500:
        raise GrokUnavailable(detail)
    raise GrokError(detail)
