#!/usr/bin/env python3
"""
Bot do Google Chat para capturar o USER_ID das pessoas.

Como funciona
-------------
Quando alguém manda uma mensagem para o bot (DM) ou o adiciona em um espaço,
o Google Chat envia um evento HTTP (POST JSON) para este servidor. O evento
contém o remetente em `message.sender`:

    {
      "sender": {
        "name": "users/123456789012345678901",   <- USER_ID que precisamos
        "displayName": "Fulano de Tal",
        "type": "HUMAN"
      }
    }

O bot extrai `displayName` e o `USER_ID` (parte depois de "users/") e grava/
atualiza o arquivo `mentions-bot.json` no formato esperado pelo main.py:

    {
      "Fulano de Tal": "123456789012345678901"
    }

Em seguida responde no chat confirmando que o ID foi capturado.

Segurança
---------
O Google Chat assina cada requisição com um JWT no header
`Authorization: Bearer <token>`. Se a variável de ambiente
CHAT_PROJECT_NUMBER (ou CHAT_AUDIENCE) estiver definida, o token é validado
(emissor chat@system.gserviceaccount.com). Recomendado em produção.

Execução
--------
    pip install flask google-auth python-dotenv
    # configure no arquivo .env (recomendado):
    #   MENTIONS_BOT_PATH=mentions-bot.json
    #   CHAT_PROJECT_NUMBER=000000000000   # opcional (recomendado)
    python tools/bot.py                    # sobe em 0.0.0.0:8080

As variáveis também podem ser exportadas no ambiente; o .env tem prioridade
apenas para chaves ainda não definidas no ambiente.

Depois exponha a porta publicamente (Cloud Run, ngrok, etc.) e configure a
URL no Google Chat API (veja o README de tools).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv
from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] chat-bot: %(message)s",
)
logger = logging.getLogger("chat-bot")

# Carrega variáveis do arquivo .env (se existir) para o ambiente.
load_dotenv()

MENTIONS_BOT_PATH = os.environ.get("MENTIONS_BOT_PATH", "mentions-bot.json")
CHAT_ISSUER = "chat@system.gserviceaccount.com"

app = Flask(__name__)
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Persistência do mentions-bot.json
# ---------------------------------------------------------------------------
def _load_mentions() -> Dict[str, str]:
    if not os.path.isfile(MENTIONS_BOT_PATH):
        return {}
    try:
        with open(MENTIONS_BOT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Falha ao ler %s (%s). Recomeçando vazio.", MENTIONS_BOT_PATH, e)
        return {}


def _save_mention(display_name: str, user_id: str) -> bool:
    """Grava/atualiza uma entrada. Retorna True se houve alteração."""
    with _lock:
        mentions = _load_mentions()
        if mentions.get(display_name) == user_id:
            return False
        mentions[display_name] = user_id
        tmp = f"{MENTIONS_BOT_PATH}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mentions, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, MENTIONS_BOT_PATH)
        return True


# ---------------------------------------------------------------------------
# Verificação opcional do token do Google Chat
# ---------------------------------------------------------------------------
def _verify_chat_token() -> bool:
    audience = os.environ.get("CHAT_AUDIENCE") or os.environ.get("CHAT_PROJECT_NUMBER")
    if not audience:
        return True  # verificação desativada

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        logger.warning("Requisição sem Bearer token.")
        return False
    token = auth.split(" ", 1)[1]
    try:
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(
            token, g_requests.Request(), audience=str(audience)
        )
        if claims.get("iss") != CHAT_ISSUER and claims.get("email") != CHAT_ISSUER:
            logger.warning("Emissor inesperado: %s", claims.get("iss"))
            return False
        return True
    except Exception as e:  # token inválido/expirado
        logger.warning("Token do Chat inválido: %s", e)
        return False


# ---------------------------------------------------------------------------
# Extração do remetente
# ---------------------------------------------------------------------------
def _extract_sender(event: dict) -> Tuple[Optional[str], Optional[str]]:
    """Retorna (display_name, user_id) a partir do evento do Chat."""
    sender = (event.get("message") or {}).get("sender") or event.get("user") or {}
    display_name = sender.get("displayName")
    name = sender.get("name", "")  # "users/123..."
    user_id = name.split("/", 1)[1] if name.startswith("users/") else None
    sender_type = sender.get("type")
    if sender_type == "BOT":  # ignora mensagens do próprio bot
        return None, None
    return display_name, user_id


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/")
def on_event():
    if not _verify_chat_token():
        return jsonify(error="unauthorized"), 401

    event = request.get_json(silent=True) or {}
    event_type = event.get("type")

    if event_type == "ADDED_TO_SPACE":
        return jsonify(
            text="👋 Olá! Me envie qualquer mensagem (ou /id) que eu capturo seu "
                 "USER_ID para o alerta de plantão."
        )

    if event_type == "REMOVED_FROM_SPACE":
        return ("", 200)

    if event_type != "MESSAGE":
        return jsonify(text="Pode me mandar uma mensagem que eu capturo seu ID. 🙂")

    display_name, user_id = _extract_sender(event)
    if not user_id:
        return jsonify(text="Não consegui identificar seu USER_ID. 🤔")

    changed = _save_mention(display_name or user_id, user_id)
    logger.info(
        "Capturado %s -> %s (%s)",
        display_name, user_id, "novo/atualizado" if changed else "já existia",
    )

    status = "registrado ✅" if changed else "já estava registrado ✅"
    return jsonify(
        text=(
            f"Pronto, *{display_name}*! Seu USER_ID foi {status}.\n"
            f"`{user_id}`\n"
            "Já pode ser mencionado no alerta de plantão."
        )
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    logger.info("Bot ouvindo em 0.0.0.0:%d — gravando em %s", port, MENTIONS_BOT_PATH)
    app.run(host="0.0.0.0", port=port)
