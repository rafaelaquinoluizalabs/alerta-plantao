#!/usr/bin/env python3
"""
Serviço web "Quem sou eu" — descobre o USER_ID do Google Chat.

A pessoa acessa http://localhost:8080/, faz login com a conta Google e a página
mostra o **nome** e o **USER_ID** dela. Esse USER_ID é o mesmo número usado nas
menções do Google Chat (`<users/USER_ID>`), pois corresponde ao `sub` (ID da
conta Google) retornado no login OpenID Connect.

A pessoa copia o ID e te envia para você cadastrar no mentions.json. Além de
exibir na tela, cada acesso autenticado é gravado de forma incremental no
arquivo `mentions-web.json` (nome -> USER_ID), pronto para uso no main.py.

Configuração (no .env)
----------------------
    GOOGLE_OAUTH_CLIENT_ID=xxxx.apps.googleusercontent.com
    GOOGLE_OAUTH_CLIENT_SECRET=xxxx
    OAUTH_REDIRECT_URI=http://localhost:8080/oauth2/callback   # opcional
    MENTIONS_WEB_PATH=mentions-web.json                        # opcional
    FLASK_SECRET_KEY=algo-aleatorio                            # opcional

Como obter as credenciais OAuth
-------------------------------
1. Google Cloud Console -> APIs & Services -> OAuth consent screen:
   configure o app (interno, se for só do seu Workspace).
2. APIs & Services -> Credentials -> Create credentials -> OAuth client ID ->
   tipo "Web application".
3. Em "Authorized redirect URIs", adicione exatamente:
       http://localhost:8080/oauth2/callback
   (ou a URL pública, se for hospedar).
4. Copie o Client ID e o Client secret para o .env.

Execução
--------
    pip install -r requirements.txt
    python tools/whoami.py        # sobe em 0.0.0.0:8080
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from typing import Dict
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from flask import Flask, redirect, request, session
from google.auth.transport import requests as g_requests
from google.oauth2 import id_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] whoami: %(message)s",
)
logger = logging.getLogger("whoami")

load_dotenv()

CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get(
    "OAUTH_REDIRECT_URI", "http://localhost:8080/oauth2/callback"
)
MENTIONS_WEB_PATH = os.environ.get("MENTIONS_WEB_PATH", "mentions-web.json")
SCOPES = "openid email profile"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Persistência incremental do mentions-web.json
# ---------------------------------------------------------------------------
def _load_mentions() -> Dict[str, str]:
    if not os.path.isfile(MENTIONS_WEB_PATH):
        return {}
    try:
        with open(MENTIONS_WEB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Falha ao ler %s (%s). Recomeçando vazio.", MENTIONS_WEB_PATH, e)
        return {}


def _save_mention(name: str, user_id: str) -> bool:
    """Grava/atualiza uma entrada nome->USER_ID. Retorna True se houve alteração."""
    with _lock:
        mentions = _load_mentions()
        if mentions.get(name) == user_id:
            return False
        mentions[name] = user_id
        tmp = f"{MENTIONS_WEB_PATH}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mentions, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, MENTIONS_WEB_PATH)
        return True


def _page(body: str) -> str:
    return f"""<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Meu USER_ID do Google Chat</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
           background:#f4f5f7; color:#1f2329; display:flex; min-height:100vh;
           align-items:center; justify-content:center; margin:0; }}
    .card {{ background:#fff; padding:32px 40px; border-radius:16px;
            box-shadow:0 8px 30px rgba(0,0,0,.08); max-width:520px; width:90%; }}
    h1 {{ font-size:20px; margin:0 0 16px; }}
    .row {{ margin:12px 0; }}
    .label {{ font-size:12px; text-transform:uppercase; color:#6b7280;
             letter-spacing:.04em; }}
    .value {{ font-size:18px; font-weight:600; word-break:break-all; }}
    .id-box {{ display:flex; gap:8px; align-items:center; }}
    code {{ background:#eef0f3; padding:8px 12px; border-radius:8px;
           font-size:18px; flex:1; word-break:break-all; }}
    button, a.btn {{ background:#1a73e8; color:#fff; border:none; cursor:pointer;
            padding:12px 20px; border-radius:8px; font-size:15px;
            text-decoration:none; display:inline-block; }}
    button.copy {{ padding:10px 14px; font-size:14px; }}
    .muted {{ color:#6b7280; font-size:13px; margin-top:18px; }}
  </style>
</head>
<body>
  <div class="card">{body}</div>
  <script>
    function copyId(id) {{
      navigator.clipboard.writeText(id).then(() => {{
        const b = document.getElementById('copyBtn');
        b.textContent = 'Copiado!';
        setTimeout(() => b.textContent = 'Copiar', 1500);
      }});
    }}
  </script>
</body>
</html>"""


@app.get("/")
def index():
    if not CLIENT_ID or not CLIENT_SECRET:
        return _page(
            "<h1>Configuração incompleta</h1>"
            "<p class='muted'>Defina <code>GOOGLE_OAUTH_CLIENT_ID</code> e "
            "<code>GOOGLE_OAUTH_CLIENT_SECRET</code> no arquivo .env.</p>"
        ), 500

    state = secrets.token_urlsafe(24)
    session["state"] = state
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "prompt": "select_account",
    }
    body = (
        "<h1>Descubra seu USER_ID do Google Chat</h1>"
        "<p>Faça login com sua conta corporativa para ver seu nome e ID.</p>"
        f"<p><a class='btn' href='{AUTH_ENDPOINT}?{urlencode(params)}'>"
        "Entrar com Google</a></p>"
    )
    return _page(body)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/oauth2/callback")
def callback():
    error = request.args.get("error")
    if error:
        return _page(f"<h1>Login cancelado</h1><p class='muted'>{error}</p>"), 400

    if request.args.get("state") != session.get("state"):
        return _page("<h1>Falha de validação (state).</h1>"
                     "<p class='muted'>Tente novamente a partir do início.</p>"), 400

    code = request.args.get("code")
    if not code:
        return _page("<h1>Código de autorização ausente.</h1>"), 400

    try:
        resp = requests.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        resp.raise_for_status()
        tokens = resp.json()
        claims = id_token.verify_oauth2_token(
            tokens["id_token"], g_requests.Request(), audience=CLIENT_ID
        )
    except requests.RequestException as e:
        logger.warning("Falha na troca de token: %s", e)
        return _page("<h1>Erro ao autenticar.</h1>"
                     "<p class='muted'>Tente novamente.</p>"), 502
    except (ValueError, KeyError) as e:
        logger.warning("Token inválido: %s", e)
        return _page("<h1>Token inválido.</h1>"), 400

    user_id = claims.get("sub", "")
    name = claims.get("name") or claims.get("email", "")
    email = claims.get("email", "")
    changed = _save_mention(name, user_id)
    logger.info(
        "Identificado %s (%s) -> %s [%s]",
        name, email, user_id, "gravado/atualizado" if changed else "já existia",
    )

    body = (
        "<h1>Pronto! 🎉</h1>"
        "<div class='row'><div class='label'>Nome</div>"
        f"<div class='value'>{name}</div></div>"
        "<div class='row'><div class='label'>E-mail</div>"
        f"<div class='value'>{email}</div></div>"
        "<div class='row'><div class='label'>USER_ID do Google Chat</div>"
        "<div class='id-box'>"
        f"<code id='uid'>{user_id}</code>"
        f"<button id='copyBtn' class='copy' onclick=\"copyId('{user_id}')\">"
        "Copiar</button></div></div>"
        "<p class='muted'>Copie o <b>USER_ID</b> acima e envie para a pessoa "
        "responsável pelo alerta de plantão.</p>"
    )
    return _page(body)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    logger.info("Serviço whoami ouvindo em 0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port)
