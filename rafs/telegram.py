"""Bot privado do Telegram: notificações + comandos somente leitura (e /pause).

Não há comando para retomar, sacar, mudar limites ou ver credenciais. Retomar é só pela CLI.
Mensagens de qualquer chat diferente de TELEGRAM_CHAT_ID são ignoradas.
"""

from __future__ import annotations

import logging
from typing import Callable

import httpx

log = logging.getLogger("rafs.telegram")

HELP = (
    "/status — estado do bot\n/balance — patrimônio simulado\n/positions — posições abertas\n"
    "/performance — comparação Jev × baseline × aleatório\n/pause — pausar novas entradas\n/help — esta ajuda\n"
    "Retomar só pela CLI no computador: python -m rafs resume"
)


class Telegram:
    def __init__(self, token: str, chat_id: str, handlers: dict[str, Callable[[], str]], on_pause: Callable[[], None],
                 get_offset: Callable[[], int], set_offset: Callable[[int], None],
                 transport: httpx.BaseTransport | None = None):
        self.chat_id = str(chat_id)
        self.handlers = handlers
        self.on_pause = on_pause
        self.get_offset, self.set_offset = get_offset, set_offset
        self._http = httpx.Client(base_url=f"https://api.telegram.org/bot{token}", timeout=15, transport=transport)

    def send(self, text: str) -> None:
        try:
            r = self._http.post("/sendMessage", json={"chat_id": self.chat_id, "text": text[:4000]})
            if r.status_code != 200:
                log.warning("sendMessage HTTP %s", r.status_code)
        except httpx.HTTPError as e:
            log.warning("sendMessage falhou: %s", type(e).__name__)  # não loga a URL (contém o token)

    def poll_commands(self) -> int:
        r = self._http.get("/getUpdates", params={"offset": self.get_offset(), "timeout": 0})
        r.raise_for_status()
        handled = 0
        for upd in r.json().get("result", []):
            self.set_offset(int(upd["update_id"]) + 1)
            msg = upd.get("message") or {}
            if str((msg.get("chat") or {}).get("id")) != self.chat_id:
                continue
            text = (msg.get("text") or "").strip()
            cmd = text.split()[0].split("@")[0].lower() if text else ""
            self.send(self.handle(cmd))
            handled += 1
        return handled

    def handle(self, cmd: str) -> str:
        if cmd == "/pause":
            self.on_pause()
            return "⏸️ Novas entradas pausadas. Saídas (stop/alvo) continuam ativas. Retomar: CLI `python -m rafs resume`."
        if cmd in ("/help", "/start"):
            return HELP
        handler = self.handlers.get(cmd)
        if handler is None:
            return "Comando não reconhecido.\n" + HELP
        return handler()
