"""Envio de notificações para vários canais (Telegram, Discord). Só saída — nenhum canal recebe comandos aqui."""

from __future__ import annotations

import logging
from typing import Callable

import httpx

log = logging.getLogger("rafs.notify")
DISCORD_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/",
                    "https://ptb.discord.com/api/webhooks/", "https://canary.discord.com/api/webhooks/")


class DiscordWebhook:
    """Webhook de um canal do Discord. A URL contém o segredo do webhook — nunca é logada."""

    def __init__(self, url: str, transport: httpx.BaseTransport | None = None):
        if not url.startswith(DISCORD_PREFIXES):
            raise ValueError("DISCORD_WEBHOOK_URL inválida (esperado https://discord.com/api/webhooks/...)")
        self._url = url
        self._http = httpx.Client(timeout=15, transport=transport)

    def send(self, text: str) -> None:
        try:
            r = self._http.post(self._url, json={"content": text[:1900], "allowed_mentions": {"parse": []}})
            if r.status_code not in (200, 204):
                log.warning("discord HTTP %s", r.status_code)
        except httpx.HTTPError as e:
            log.warning("discord falhou: %s", type(e).__name__)

    def check(self) -> str:
        """GET no webhook devolve nome/canal sem postar mensagem."""
        r = self._http.get(self._url)
        r.raise_for_status()
        return str(r.json().get("name", "?"))


def fan_out(*senders: Callable[[str], None] | None) -> Callable[[str], None] | None:
    active = [s for s in senders if s is not None]
    if not active:
        return None

    def send(text: str) -> None:
        for s in active:
            try:
                s(text)
            except Exception as e:  # noqa: BLE001 — um canal fora do ar não derruba o bot
                log.warning("notificação falhou: %s", type(e).__name__)
    return send
