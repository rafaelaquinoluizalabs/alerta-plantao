"""
Ferramenta de apoio para inspecionar (e futuramente ajustar) os plantonistas
no VictorOps / Splunk On-Call por competência.

Boas práticas aplicadas:
- Não desativa verificação TLS (sem ``verify=False``).
- Todas as chamadas HTTP usam timeout.
- Times do VictorOps são configuráveis por variável de ambiente.
- Erros de rede são tratados de forma específica.
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

try:
    # Quando executado como módulo (python -m tools.ajustar_plantonistas)
    from .planilha_map import COMPETENCIA_PLANILHA_MAP
except ImportError:
    # Quando executado como script (python tools/ajustar_plantonistas.py)
    from planilha_map import COMPETENCIA_PLANILHA_MAP


API_BASE = "https://api.victorops.com/api-public/v1"
HTTP_TIMEOUT = 15  # segundos

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ajustar-plantonistas")


def get_tls_verify() -> bool | str:
    """
    Resolve a verificação TLS de forma segura por padrão.

    - ``VICTOROPS_CA_BUNDLE``: caminho para um bundle de CA corporativa
      (recomendado em redes com proxy/certificado interno).
    - ``VICTOROPS_INSECURE=true``: desativa a verificação TLS (NÃO recomendado;
      use apenas conscientemente em ambientes controlados).
    """
    ca_bundle = os.getenv("VICTOROPS_CA_BUNDLE", "").strip()
    if ca_bundle:
        return ca_bundle
    if os.getenv("VICTOROPS_INSECURE", "").strip().lower() in {"1", "true", "yes"}:
        logger.warning(
            "Verificação TLS DESATIVADA (VICTOROPS_INSECURE). Use apenas em "
            "ambientes controlados; prefira VICTOROPS_CA_BUNDLE."
        )
        try:
            from urllib3.exceptions import InsecureRequestWarning

            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - best effort
            pass
        return False
    return True


def get_teams() -> List[str]:
    """
    Times (slugs) do VictorOps a consultar.

    Por padrão usa as chaves do mapa de competências da planilha; pode ser
    sobrescrito pela variável de ambiente ``VICTOROPS_TEAMS`` (separada por
    vírgulas).
    """
    raw = os.getenv("VICTOROPS_TEAMS", "")
    if raw.strip():
        return [t.strip() for t in raw.split(",") if t.strip()]
    return list(COMPETENCIA_PLANILHA_MAP.keys())


def get_env_vars() -> tuple[str, str, str]:
    load_dotenv()
    api_id = os.getenv("VICTOROPS_API_ID")
    api_key = os.getenv("VICTOROPS_API_KEY")
    org_id = os.getenv("VICTOROPS_ORG_ID")
    if not all([api_id, api_key, org_id]):
        raise EnvironmentError(
            "Faltam variáveis de ambiente VICTOROPS_API_ID, "
            "VICTOROPS_API_KEY ou VICTOROPS_ORG_ID"
        )
    return api_id, api_key, org_id


def get_headers(api_id: str, api_key: str, org_id: str) -> Dict[str, str]:
    return {
        "X-VO-Api-Id": api_id,
        "X-VO-Api-Key": api_key,
        "X-VO-Org-Id": org_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def get_users(api_id: str, api_key: str, org_id: str) -> List[dict]:
    """Lista todos os usuários da organização."""
    url = f"{API_BASE}/user"
    resp = requests.get(
        url,
        headers=get_headers(api_id, api_key, org_id),
        timeout=HTTP_TIMEOUT,
        verify=get_tls_verify(),
    )
    resp.raise_for_status()
    return resp.json().get("users", [])


def get_all_teams(api_id: str, api_key: str, org_id: str) -> List[dict]:
    """Lista todos os times do VictorOps (com ``slug`` e ``name``)."""
    url = f"{API_BASE}/team"
    resp = requests.get(
        url,
        headers=get_headers(api_id, api_key, org_id),
        timeout=HTTP_TIMEOUT,
        verify=get_tls_verify(),
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("teams", [])


def build_name_to_slug(teams: List[dict]) -> Dict[str, str]:
    """Monta um mapa nome-do-time -> slug (case-insensitive)."""
    mapping: Dict[str, str] = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        name = team.get("name")
        slug = team.get("slug")
        if name and slug:
            mapping[name.strip().lower()] = slug
    return mapping


def get_team_oncall_schedule(
    api_id: str, api_key: str, org_id: str, team_slug: str
) -> dict:
    """Busca a escala de plantão (on-call) de um time pelo seu ``slug``."""
    url = f"{API_BASE}/team/{team_slug}/oncall/schedule"
    resp = requests.get(
        url,
        headers=get_headers(api_id, api_key, org_id),
        timeout=HTTP_TIMEOUT,
        verify=get_tls_verify(),
    )
    resp.raise_for_status()
    return resp.json()


def get_current_oncall(schedule: dict) -> List[str]:
    """Extrai os plantonistas atualmente em escala (campo ``onCall``)."""
    oncall: List[str] = []
    for entry in schedule.get("schedule", []):
        user = entry.get("onCall")
        if user and user not in oncall:
            oncall.append(user)
    return oncall


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspeciona (e futuramente ajusta) plantonistas VictorOps por competência."
    )
    parser.add_argument(
        "--ajustar",
        action="store_true",
        help="Ajusta automaticamente o VictorOps para refletir a planilha.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula o ajuste sem alterar o VictorOps (Splunk).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    try:
        api_id, api_key, org_id = get_env_vars()
    except EnvironmentError as e:
        logger.error("%s", e)
        return 1

    try:
        name_to_slug = build_name_to_slug(get_all_teams(api_id, api_key, org_id))
    except requests.RequestException as e:
        logger.error("Não foi possível listar os times do VictorOps: %s", e)
        return 1

    for team in get_teams():
        slug = name_to_slug.get(team.strip().lower(), team)
        logger.info("=== %s (%s) ===", team, slug)
        try:
            schedule = get_team_oncall_schedule(api_id, api_key, org_id, slug)
        except requests.RequestException as e:
            logger.error("Falha ao buscar escala do time '%s': %s", team, e)
            continue
        oncall = get_current_oncall(schedule)
        if oncall:
            logger.info("Plantonista(s) atual(is): %s", ", ".join(oncall))
        else:
            logger.info("Nenhum plantonista em escala.")

    if args.ajustar and not args.dry_run:
        logger.warning(
            "Ajuste automático ainda não implementado: nenhuma alteração foi "
            "feita no VictorOps."
        )
    elif args.ajustar and args.dry_run:
        logger.info("[dry-run] Nenhuma alteração seria aplicada ao VictorOps.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
