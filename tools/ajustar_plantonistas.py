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
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

try:
    # Quando executado como módulo (python -m tools.ajustar_plantonistas)
    from .planilha_map import COMPETENCIA_PLANILHA_MAP
    from .usuario_map import resolver_username
except ImportError:
    # Quando executado como script (python tools/ajustar_plantonistas.py)
    from planilha_map import COMPETENCIA_PLANILHA_MAP
    from usuario_map import resolver_username


API_BASE = "https://api.victorops.com/api-public/v1"
OVERRIDE_TIMEZONE = "America/Sao_Paulo"
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


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Converte uma string ISO 8601 (com offset) em ``datetime`` aware."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def get_next_week_oncall(schedule: dict) -> Optional[Dict[str, str]]:
    """
    Retorna o próximo turno (a próxima troca de plantão) da escala.

    Procura, entre todas as rotações do time, o ``roll`` cujo início
    (``change``) é o primeiro no futuro. Retorna ``{onCall, start, end}``
    (datas em ISO 8601) ou ``None`` se não houver turno futuro.
    """
    agora = datetime.now(timezone.utc)
    melhor: Optional[Dict[str, str]] = None
    melhor_inicio: Optional[datetime] = None

    for entry in schedule.get("schedule", []):
        for roll in entry.get("rolls", []):
            inicio = _parse_iso(roll.get("change"))
            if inicio is None or inicio <= agora:
                continue
            if melhor_inicio is None or inicio < melhor_inicio:
                melhor_inicio = inicio
                melhor = {
                    "onCall": roll.get("onCall", ""),
                    "start": roll.get("change", ""),
                    "end": roll.get("until", ""),
                }
    return melhor


def create_override(
    api_id: str,
    api_key: str,
    org_id: str,
    username: str,
    start: str,
    end: str,
) -> dict:
    """
    Cria um scheduled override para ``username`` no intervalo informado.

    Retorna o objeto do override criado (com ``publicId`` e ``assignments``).
    """
    url = f"{API_BASE}/overrides"
    payload = {
        "username": username,
        "timezone": OVERRIDE_TIMEZONE,
        "start": start,
        "end": end,
    }
    resp = requests.post(
        url,
        headers=get_headers(api_id, api_key, org_id),
        json=payload,
        timeout=HTTP_TIMEOUT,
        verify=get_tls_verify(),
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("schedule") or data.get("override") or data


def assign_override(
    api_id: str,
    api_key: str,
    org_id: str,
    public_id: str,
    policy_slug: str,
    username: str,
) -> dict:
    """Atribui ``username`` como cobertura do override na policy informada."""
    url = f"{API_BASE}/overrides/{public_id}/assignments/{policy_slug}"
    payload = {"username": username, "acceptOverlap": True}
    resp = requests.put(
        url,
        headers=get_headers(api_id, api_key, org_id),
        json=payload,
        timeout=HTTP_TIMEOUT,
        verify=get_tls_verify(),
    )
    resp.raise_for_status()
    return resp.json()


def read_next_week_plantonistas() -> Dict[str, str]:
    """
    Lê da planilha Google os plantonistas da próxima semana por competência.

    Reaproveita a lógica de ``main.py`` (mesma identificação de semana).
    Retorna ``{competência: nome}``.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from main import PlantaoExtractor, Settings, SheetsClient

    settings = Settings.from_env(require_webhook=False)
    worksheet = SheetsClient(
        settings.credentials_path,
        settings.spreadsheet_id,
        settings.sheet_tab_name,
    ).open_worksheet()
    extractor = PlantaoExtractor(worksheet)
    return extractor.next_week_by_competency(COMPETENCIA_PLANILHA_MAP)


def ajustar_proxima_semana(
    api_id: str,
    api_key: str,
    org_id: str,
    name_to_slug: Dict[str, str],
    dry_run: bool,
) -> int:
    """
    Garante que os plantonistas da próxima semana (planilha) estejam na
    escala do VictorOps, criando scheduled overrides quando necessário.

    Retorna 0 em caso de sucesso, 1 se houver falhas relevantes.
    """
    try:
        proxima_semana = read_next_week_plantonistas()
    except Exception as e:  # noqa: BLE001 - erros de planilha/credenciais variados
        logger.error("Não foi possível ler os plantonistas da planilha: %s", e)
        return 1

    houve_erro = False

    for competencia, nome_planilha in proxima_semana.items():
        slug = name_to_slug.get(competencia.strip().lower())
        if not slug:
            logger.warning(
                "Competência '%s' não corresponde a nenhum time do VictorOps; "
                "pulando.",
                competencia,
            )
            continue

        if not nome_planilha:
            logger.warning(
                "Sem plantonista na planilha para '%s' na próxima semana; pulando.",
                competencia,
            )
            continue

        desejado = resolver_username(nome_planilha)
        if not desejado:
            logger.warning(
                "Nome '%s' (competência '%s') não está em usuario_map.py; "
                "adicione o mapeamento nome->username. Pulando.",
                nome_planilha,
                competencia,
            )
            houve_erro = True
            continue

        try:
            schedule = get_team_oncall_schedule(api_id, api_key, org_id, slug)
        except requests.RequestException as e:
            logger.error("Falha ao buscar escala de '%s': %s", competencia, e)
            houve_erro = True
            continue

        proximo = get_next_week_oncall(schedule)
        if not proximo or not proximo.get("start"):
            logger.warning(
                "Não há próximo turno definido para '%s'; pulando.", competencia
            )
            continue

        atual = proximo.get("onCall", "")
        inicio, fim = proximo["start"], proximo["end"]

        if atual == desejado:
            logger.info(
                "[%s] Próxima semana já está com '%s' (%s a %s); nada a fazer.",
                competencia, desejado, inicio, fim,
            )
            continue

        if dry_run:
            logger.info(
                "[dry-run][%s] Criaria override: '%s' -> '%s' de %s a %s.",
                competencia, atual or "(vazio)", desejado, inicio, fim,
            )
            continue

        try:
            override = create_override(
                api_id, api_key, org_id, atual, inicio, fim
            )
        except requests.RequestException as e:
            logger.error(
                "[%s] Falha ao criar override para '%s': %s",
                competencia, atual, e,
            )
            houve_erro = True
            continue

        public_id = override.get("publicId", "")
        assignments = [
            a
            for a in override.get("assignments", [])
            if isinstance(a, dict) and a.get("team") == slug and a.get("policy")
        ]
        if not public_id or not assignments:
            logger.error(
                "[%s] Override criado (%s), mas sem assignment para o time %s.",
                competencia, public_id or "?", slug,
            )
            houve_erro = True
            continue

        for assignment in assignments:
            policy_slug = assignment["policy"]
            try:
                assign_override(
                    api_id, api_key, org_id, public_id, policy_slug, desejado
                )
                logger.info(
                    "[%s] Override aplicado: '%s' cobre '%s' (%s) de %s a %s.",
                    competencia, desejado, atual or "(vazio)", policy_slug,
                    inicio, fim,
                )
            except requests.RequestException as e:
                logger.error(
                    "[%s] Falha ao atribuir '%s' na policy %s: %s",
                    competencia, desejado, policy_slug, e,
                )
                houve_erro = True

    return 1 if houve_erro else 0


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

    if args.ajustar:
        logger.info(
            "Ajustando plantonistas da próxima semana%s...",
            " (dry-run)" if args.dry_run else "",
        )
        return ajustar_proxima_semana(
            api_id, api_key, org_id, name_to_slug, dry_run=args.dry_run
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
