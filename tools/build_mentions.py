#!/usr/bin/env python3
"""
Gera (ou atualiza) o `mentions.json` resolvendo e-mails em USER_IDs do
Google Workspace via Admin SDK Directory API.

Requer uma Service Account com **domain-wide delegation** habilitada e o
escopo `https://www.googleapis.com/auth/admin.directory.user.readonly`
autorizado no Admin Console (Security → API Controls → Domain-wide
Delegation). A SA impersona um usuário admin (ADMIN_EMAIL) para consultar
o diretório.

Entrada
-------
Um CSV com cabeçalho contendo, no mínimo, as colunas `Nome` e `Email`
(aceita variações como `nome`, `email`, `e-mail`). Exemplo:

    Nome,Email
    João Silva,joao.silva@empresa.com
    Maria Souza,maria.souza@empresa.com

A coluna `Nome` deve bater com o nome **como aparece na planilha** (é a
chave usada no `mentions.json`). O e-mail é usado só para resolver o ID.

Uso
---
    python tools/build_mentions.py --csv equipe.csv
    python tools/build_mentions.py --csv equipe.csv --output mentions.json
    python tools/build_mentions.py --csv equipe.csv --dry-run

Variáveis de ambiente (lidas do .env):
    CREDENTIALS_PATH   Caminho do JSON da Service Account (default ./credentials.json)
    ADMIN_EMAIL        E-mail do admin a ser impersonado (obrigatório)
    MENTIONS_PATH      Caminho de saída padrão (default ./mentions.json)
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("build-mentions")

DIRECTORY_SCOPE = "https://www.googleapis.com/auth/admin.directory.user.readonly"
DIRECTORY_USER_URL = "https://admin.googleapis.com/admin/directory/v1/users/{user_key}"

# Aliases aceitos para as colunas do CSV (comparados em minúsculas, sem espaços).
NAME_HEADERS = {"nome", "name", "plantonista"}
EMAIL_HEADERS = {"email", "e-mail", "mail"}


def _normalize_header(h: str) -> str:
    return h.strip().lower().lstrip("\ufeff")


def read_csv(path: str) -> List[Tuple[str, str]]:
    """Lê o CSV e retorna lista de (nome, email). Ignora linhas incompletas."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"CSV não encontrado: {path}")

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("CSV vazio.")

        norm = [_normalize_header(h) for h in header]
        name_idx = next((i for i, h in enumerate(norm) if h in NAME_HEADERS), None)
        email_idx = next((i for i, h in enumerate(norm) if h in EMAIL_HEADERS), None)

        if name_idx is None or email_idx is None:
            raise ValueError(
                "O CSV precisa de colunas de nome e e-mail. "
                f"Cabeçalho lido: {header}. "
                f"Esperado algo em {sorted(NAME_HEADERS)} e {sorted(EMAIL_HEADERS)}."
            )

        rows: List[Tuple[str, str]] = []
        for line_no, row in enumerate(reader, start=2):
            if name_idx >= len(row) or email_idx >= len(row):
                logger.warning("Linha %d incompleta, ignorando: %r", line_no, row)
                continue
            name = row[name_idx].strip()
            email = row[email_idx].strip()
            if not name or not email:
                logger.warning("Linha %d sem nome/e-mail, ignorando.", line_no)
                continue
            rows.append((name, email))

    if not rows:
        raise ValueError("Nenhuma linha válida encontrada no CSV.")
    return rows


def build_session(credentials_path: str, admin_email: str) -> AuthorizedSession:
    """Autentica a SA com domain-wide delegation e devolve uma sessão HTTP.

    Usa AuthorizedSession para que o token seja renovado automaticamente
    durante execuções longas (diretórios grandes).
    """
    if not os.path.isfile(credentials_path):
        raise FileNotFoundError(
            f"Arquivo de credenciais não encontrado em: {credentials_path}"
        )
    creds = Credentials.from_service_account_file(
        credentials_path, scopes=[DIRECTORY_SCOPE]
    ).with_subject(admin_email)
    return AuthorizedSession(creds)


def resolve_user_id(session: AuthorizedSession, email: str, timeout: int = 15) -> Optional[str]:
    """Resolve um e-mail para o ID numérico do usuário. None se não encontrado."""
    url = DIRECTORY_USER_URL.format(user_key=email)
    resp = session.get(url, params={"fields": "id,primaryEmail"}, timeout=timeout)
    if resp.status_code == 404:
        logger.warning("Usuário não encontrado no diretório: %s", email)
        return None
    if not resp.ok:
        logger.error(
            "Erro ao consultar %s: status %s — %s",
            email, resp.status_code, resp.text[:300],
        )
        resp.raise_for_status()
    return str(resp.json().get("id", "")) or None


def build_mentions(rows: List[Tuple[str, str]], session: AuthorizedSession) -> Dict[str, str]:
    mentions: Dict[str, str] = {}
    for name, email in rows:
        user_id = resolve_user_id(session, email)
        if user_id:
            mentions[name] = user_id
            logger.info("OK  %-30s -> %s", name, user_id)
        else:
            logger.warning("SKIP %-30s (sem ID)", name)
    return mentions


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera mentions.json resolvendo e-mails em USER_IDs via Directory API."
    )
    parser.add_argument("--csv", required=True, help="CSV com colunas Nome,Email.")
    parser.add_argument(
        "--output",
        help="Caminho de saída (default: MENTIONS_PATH ou ./mentions.json).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve e imprime o JSON no console, sem gravar arquivo.",
    )
    return parser.parse_args(argv)


def run(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    load_dotenv()

    credentials_path = os.getenv("CREDENTIALS_PATH", "./credentials.json")
    admin_email = os.getenv("ADMIN_EMAIL", "")
    output_path = args.output or os.getenv("MENTIONS_PATH", "./mentions.json")

    if not admin_email:
        logger.error(
            "ADMIN_EMAIL ausente. Defina no .env o e-mail do admin a ser "
            "impersonado pela Service Account (domain-wide delegation)."
        )
        return 1

    try:
        rows = read_csv(args.csv)
        logger.info("Lidas %d pessoas do CSV.", len(rows))
        session = build_session(credentials_path, admin_email)
        mentions = build_mentions(rows, session)
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 1
    except ValueError as e:
        logger.error("Erro no CSV: %s", e)
        return 1
    except GoogleAuthError as e:
        logger.error(
            "Falha de autenticação/delegation: %s. Verifique se o Client ID da "
            "Service Account está autorizado no Admin Console com o escopo "
            "'%s' e se ADMIN_EMAIL é um admin válido do domínio.",
            e, DIRECTORY_SCOPE,
        )
        return 1
    except requests.RequestException as e:
        logger.error("Falha de rede/autorização ao consultar a Directory API: %s", e)
        return 1

    if not mentions:
        logger.error("Nenhum ID resolvido. Verifique e-mails, escopo e delegation.")
        return 1

    payload = json.dumps(mentions, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        print(payload)
        logger.info("--dry-run: arquivo NÃO gravado.")
        return 0

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(payload)
    logger.info("Gravadas %d menções em '%s'.", len(mentions), output_path)
    return 0


if __name__ == "__main__":
    sys.exit(run())
