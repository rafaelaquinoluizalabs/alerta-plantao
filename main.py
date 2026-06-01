"""
Automação de aviso semanal de plantonistas no Google Chat.

Lê a aba 'HOJE' de uma planilha Google Sheets, identifica a semana
corrente com base na data de execução, monta uma mensagem com os
plantonistas e a publica em um Incoming Webhook do Google Chat.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import gspread
import requests
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials


# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("alerta-plantao")


# ---------------------------------------------------------------------------
# Constantes da estrutura da planilha (1-based, como o gspread espera)
# ---------------------------------------------------------------------------
DATA_START_ROW = 4      # Linha 4 em diante: dias do mês
COL_MONTH = 2           # Coluna B: mês (pode estar mesclada)
DAYS_COL_START = 3      # Coluna C
DAYS_COL_END = 9        # Coluna I

COL_CLOUD = 11          # K
COL_ONPREM = 12         # L
COL_DADOS = 13          # M
COL_SQUAD_LEAD = 16     # P
COL_TL = 17             # Q

# Janela (em horas) na qual uma mensagem idêntica não é reenviada,
# evitando duplicações por execuções repetidas / retries / agendadores.
DEDUP_WINDOW_HOURS = 12
# Arquivo de estado que guarda o hash e o horário do último envio.
STATE_FILE = ".last_sent.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Nomes de meses em português (índice = número do mês)
MONTH_NAMES_PT = [
    "",
    "JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL",
    "MAIO", "JUNHO", "JULHO", "AGOSTO",
    "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    webhook_url: str
    spreadsheet_id: str
    credentials_path: str
    sheet_tab_name: str
    mentions_path: str

    @classmethod
    def from_env(cls, require_webhook: bool = True) -> "Settings":
        load_dotenv()
        webhook_url = os.getenv("GOOGLE_CHAT_WEBHOOK_URL", "")
        spreadsheet_id = os.getenv("SPREADSHEET_ID", "")
        credentials_path = os.getenv("CREDENTIALS_PATH", "")

        missing: List[str] = []
        if not spreadsheet_id:
            missing.append("SPREADSHEET_ID")
        if not credentials_path:
            missing.append("CREDENTIALS_PATH")
        if require_webhook and not webhook_url:
            missing.append("GOOGLE_CHAT_WEBHOOK_URL")
        if missing:
            raise EnvironmentError(
                f"Variáveis de ambiente ausentes: {', '.join(missing)}"
            )

        return cls(
            webhook_url=webhook_url,
            spreadsheet_id=spreadsheet_id,
            credentials_path=credentials_path,
            sheet_tab_name=os.getenv("SHEET_TAB_NAME", "HOJE"),
            mentions_path=os.getenv("MENTIONS_PATH", "mentions.json"),
        )


@dataclass(frozen=True)
class Plantonistas:
    squad_lead: str
    cloud: str
    onprem: str
    dados: str
    tl: str
    cloud_sabado: str
    onprem_sabado: str
    dados_sabado: str


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------
class SheetsClient:
    def __init__(self, credentials_path: str, spreadsheet_id: str, tab_name: str):
        self._credentials_path = credentials_path
        self._spreadsheet_id = spreadsheet_id
        self._tab_name = tab_name

    def open_worksheet(self) -> gspread.Worksheet:
        if not os.path.isfile(self._credentials_path):
            raise FileNotFoundError(
                f"Arquivo de credenciais não encontrado em: {self._credentials_path}"
            )
        logger.info("Autenticando na Google API com Service Account...")
        creds = Credentials.from_service_account_file(
            self._credentials_path, scopes=SCOPES
        )
        client = gspread.authorize(creds)
        logger.info("Abrindo planilha %s / aba '%s'", self._spreadsheet_id, self._tab_name)
        sheet = client.open_by_key(self._spreadsheet_id)
        return sheet.worksheet(self._tab_name)


# ---------------------------------------------------------------------------
# Lógica de negócio
# ---------------------------------------------------------------------------
class PlantaoExtractor:
    """Extrai os plantonistas da semana corrente e do sábado seguinte."""

    def __init__(self, worksheet: gspread.Worksheet, today: Optional[date] = None):
        self._ws = worksheet
        self._today = today or date.today()

    # -------- API pública --------
    def next_week_by_competency(
        self, competencia_map: Dict[str, Dict[str, object]]
    ) -> Dict[str, str]:
        """
        Retorna, para cada competência do ``competencia_map``, o nome do
        plantonista da **próxima** semana (linha seguinte à semana corrente
        na planilha), usando a coluna definida em ``info['col']``.
        """
        all_values: List[List[str]] = self._ws.get_all_values()
        week_row_idx = self._find_current_week_row(all_values)
        if week_row_idx is None:
            raise LookupError(
                f"Não foi possível localizar o dia {self._today.day:02d}/"
                f"{self._today.month:02d} ({MONTH_NAMES_PT[self._today.month]}) "
                f"nas colunas C-I da aba '{self._ws.title}'."
            )
        next_row = (
            all_values[week_row_idx + 1]
            if week_row_idx + 1 < len(all_values)
            else []
        )
        return {
            comp: self._cell(next_row, int(info["col"]))
            for comp, info in competencia_map.items()
        }

    def extract(self) -> Plantonistas:
        all_values: List[List[str]] = self._ws.get_all_values()
        week_row_idx = self._find_current_week_row(all_values)
        if week_row_idx is None:
            raise LookupError(
                f"Não foi possível localizar o dia {self._today.day:02d}/"
                f"{self._today.month:02d} ({MONTH_NAMES_PT[self._today.month]}) "
                f"nas colunas C-I da aba '{self._ws.title}'."
            )

        logger.info("Linha da semana atual encontrada: %d", week_row_idx + 1)

        current_row = all_values[week_row_idx]
        next_row = (
            all_values[week_row_idx + 1]
            if week_row_idx + 1 < len(all_values)
            else []
        )

        self._log_week_range(current_row, next_row)

        # Regra de negócio: o plantão do sábado/domingo já é da
        # rotação da próxima semana (linha seguinte na planilha).
        return Plantonistas(
            squad_lead=self._cell(current_row, COL_SQUAD_LEAD),
            cloud=self._cell(current_row, COL_CLOUD),
            onprem=self._cell(current_row, COL_ONPREM),
            dados=self._cell(current_row, COL_DADOS),
            tl=self._cell(current_row, COL_TL),
            cloud_sabado=self._cell(next_row, COL_CLOUD),
            onprem_sabado=self._cell(next_row, COL_ONPREM),
            dados_sabado=self._cell(next_row, COL_DADOS),
        )

    # -------- Helpers --------
    @staticmethod
    def _remove_parens(text: str) -> str:
        """Remove tudo entre parênteses (inclusive) e espaços antes/depois."""
        import re
        # Remove tudo entre parênteses, inclusive parênteses e espaços antes
        return re.sub(r"\s*\([^)]*\)", "", text).strip()

    @classmethod
    def _cell(cls, row: List[str], col_1based: int) -> str:
        idx = col_1based - 1
        if 0 <= idx < len(row):
            val = row[idx].strip()
            return cls._remove_parens(val)
        return ""

    def _log_week_range(self, current_row: List[str], next_row: List[str]) -> None:
        """
        Loga os dias (C..I) da semana corrente e da próxima linha
        (de onde sai o sábado). Emite WARNING se a estrutura não
        parecer Seg..Dom em 7 colunas.
        """
        cur_days = [self._parse_day(self._cell(current_row, c))
                    for c in range(DAYS_COL_START, DAYS_COL_END + 1)]
        nxt_days = [self._parse_day(self._cell(next_row, c))
                    for c in range(DAYS_COL_START, DAYS_COL_END + 1)]

        cur_str = " ".join(f"{d:02d}" if d else "--" for d in cur_days)
        logger.info("Semana corrente (C..I): %s", cur_str)

        filled = [d for d in cur_days if d is not None]
        if len(filled) != 7:
            logger.warning(
                "Semana corrente tem %d dias preenchidos (esperado 7 = Seg..Dom). "
                "A regra de neg\u00f3cio assume linha = uma semana completa.",
                len(filled),
            )

        if next_row:
            nxt_str = " ".join(f"{d:02d}" if d else "--" for d in nxt_days)
            logger.info("Próxima semana (sábado virá daqui): %s", nxt_str)
        else:
            logger.warning(
                "Não há próxima linha após a semana atual; "
                "plantonistas de sábado virão em branco."
            )

    def _find_current_week_row(self, all_values: List[List[str]]) -> Optional[int]:
        """
        Percorre as linhas de dias (a partir de DATA_START_ROW) e procura
        a linha cuja semana contém o dia de hoje, **dentro do mês atual**.

        A coluna B pode estar mesclada no Google Sheets, então
        `get_all_values()` retorna o nome do mês apenas na primeira linha
        do bloco e vazio nas demais. Mantemos um "contexto de mês atual"
        que só é atualizado quando a célula B vem preenchida.

        Como a planilha não contém o ano (e o mesmo nome de mês pode
        aparecer várias vezes para anos diferentes), desambiguamos
        exigindo que a posição do dia (offset 0..6 nas colunas C..I,
        onde C=segunda e I=domingo) corresponda ao dia da semana real
        de hoje. Isso identifica unicamente o ano correto.
        """
        target_day = self._today.day
        target_month_name = MONTH_NAMES_PT[self._today.month]
        target_weekday = self._today.weekday()  # 0=Seg ... 6=Dom
        start_idx = DATA_START_ROW - 1  # 0-based
        current_month_matches = False
        fallback_row: Optional[int] = None

        for row_idx in range(start_idx, len(all_values)):
            row = all_values[row_idx]

            month_cell = self._cell(row, COL_MONTH)
            if month_cell:
                current_month_matches = self._month_matches(month_cell, target_month_name)
                logger.debug(
                    "Linha %d: mês='%s' (match=%s)",
                    row_idx + 1, month_cell, current_month_matches,
                )

            if not current_month_matches:
                continue

            for col_1based in range(DAYS_COL_START, DAYS_COL_END + 1):
                value = self._cell(row, col_1based)
                day = self._parse_day(value)
                if day != target_day:
                    continue
                offset = col_1based - DAYS_COL_START  # 0=Seg ... 6=Dom
                if offset == target_weekday:
                    logger.debug(
                        "Match (weekday OK): linha=%d coluna=%d valor='%s'",
                        row_idx + 1, col_1based, value,
                    )
                    return row_idx
                logger.debug(
                    "Linha %d tem o dia %d na coluna %d (offset=%d), mas o "
                    "weekday não bate com hoje (%d). Provavelmente é outro ano.",
                    row_idx + 1, target_day, col_1based, offset, target_weekday,
                )
                if fallback_row is None:
                    fallback_row = row_idx

        if fallback_row is not None:
            logger.warning(
                "Nenhuma linha com weekday alinhado para %s. Usando primeiro "
                "match por dia/mês (linha %d) como fallback — pode ser ano errado.",
                self._today.isoformat(), fallback_row + 1,
            )
            return fallback_row
        return None

    @staticmethod
    def _month_matches(cell_value: str, target_month_name: str) -> bool:
        """
        Compara o texto da coluna B com o nome do mês alvo, ignorando
        acentos e capitalização. Aceita formatos como 'MAIO', 'Maio',
        'MAIO/2026', 'Maio 2026' etc.
        """
        import unicodedata

        def norm(s: str) -> str:
            s = unicodedata.normalize("NFD", s)
            return "".join(c for c in s if unicodedata.category(c) != "Mn").upper()

        return norm(target_month_name) in norm(cell_value)

    @staticmethod
    def _parse_day(value: str) -> Optional[int]:
        """Converte '15', '15.0', '15/05', '15/05/2026' etc. em inteiro do dia."""
        if not value:
            return None
        token = value.strip().split("/")[0].split("-")[0]
        try:
            return int(float(token))
        except (ValueError, TypeError):
            return None


# ---------------------------------------------------------------------------
# Google Chat
# ---------------------------------------------------------------------------
class ChatNotifier:
    def __init__(self, webhook_url: str, timeout: int = 10):
        self._webhook_url = webhook_url
        self._timeout = timeout

    def send(self, text: str) -> None:
        payload = {"text": text}
        logger.info("Enviando mensagem para o Google Chat...")
        response = requests.post(self._webhook_url, json=payload, timeout=self._timeout)
        if not response.ok:
            logger.error(
                "Google Chat retornou status %s. Resposta: %s",
                response.status_code,
                response.text[:500],
            )
        response.raise_for_status()
        logger.info("Mensagem enviada com sucesso (status %s).", response.status_code)


# ---------------------------------------------------------------------------
# Resolução de menções (@) no Google Chat
# ---------------------------------------------------------------------------
class MentionResolver:
    """
    Resolve um nome (como aparece na planilha) para a sintaxe de menção
    aceita pelo Google Chat via Incoming Webhook: ``<users/USER_ID>``.

    Se o nome não existir no mapa, retorna ``@{nome}`` como fallback de texto
    e emite WARNING (uma vez por nome por execução).
    """

    def __init__(
        self,
        mapping: Optional[Dict[str, str]] = None,
        *,
        mapping_available: bool = True,
    ):
        self._raw_map: Dict[str, str] = dict(mapping or {})
        # Mapa normalizado: chave canonicalizada -> USER_ID
        self._norm_map: Dict[str, str] = {
            self._normalize(k): v for k, v in self._raw_map.items() if k
        }
        # Quando False (ex.: mentions.json ausente), o fallback é o nome puro,
        # sem o prefixo '@', para que a mensagem fique igual à planilha.
        self._mapping_available = mapping_available
        self._warned: set[str] = set()

    @classmethod
    def from_path(cls, path: str) -> "MentionResolver":
        if not path or not os.path.isfile(path):
            logger.warning(
                "Arquivo de menções não encontrado em '%s'. "
                "Mensagens usarão apenas o nome como está na planilha, "
                "sem notificar os usuários.",
                path,
            )
            return cls({}, mapping_available=False)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "Falha ao ler '%s' (%s). Mensagens usarão apenas o nome "
                "como está na planilha.",
                path, e,
            )
            return cls({}, mapping_available=False)
        if not isinstance(data, dict):
            logger.warning(
                "Conteúdo de '%s' não é um objeto JSON. Ignorando.", path,
            )
            return cls({}, mapping_available=False)
        # Garante que todos os valores sejam string
        mapping = {str(k): str(v) for k, v in data.items()}
        logger.info("Carregadas %d menções de '%s'.", len(mapping), path)
        return cls(mapping, mapping_available=True)

    def resolve(self, name: str) -> str:
        if not name:
            return ""
        user_id = self._norm_map.get(self._normalize(name))
        if user_id:
            return f"<users/{user_id}>"
        # Sem mapa carregado: devolve o nome puro (comportamento legado).
        if not self._mapping_available:
            return name
        # Com mapa carregado mas nome ausente: exibe o nome exatamente como
        # está na planilha (sem mencionar) e emite WARNING único para
        # sinalizar que faltou cadastrar o USER_ID.
        if name not in self._warned:
            logger.warning(
                "Nome '%s' não encontrado no mapa de menções; "
                "exibindo o nome sem mencionar.",
                name,
            )
            self._warned.add(name)
        return name

    @staticmethod
    def _normalize(s: str) -> str:
        s = unicodedata.normalize("NFD", s)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        return " ".join(s.split()).casefold()


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------
def format_message(p: Plantonistas, resolver: MentionResolver) -> str:
    r = resolver.resolve
    return (
        "💙 Plantão da Semana 💙\n"
        "\n"
        f"*SL*:  {r(p.squad_lead)}\n"
        f"*Cloud*: {r(p.cloud)}\n"
        f"*Onprem*: {r(p.onprem)}\n"
        f"*Dados*: {r(p.dados)}\n"
        f"*TL iPET*: {r(p.tl)}\n"
        "\n"
        "💙 Plantão do sábado 💙\n"
        "\n"
        f"*Cloud*: {r(p.cloud_sabado)}\n"
        f"*Onprem*: {r(p.onprem_sabado)}\n"
        f"*Dados*: {r(p.dados_sabado)}\n"
        "\n\nBoa semana pessoal. Bora pra cima. 🚀🚀🚀"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Envia o aviso semanal de plantonistas ao Google Chat."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lê a planilha e imprime a mensagem no console, sem enviar ao Google Chat.",
    )
    parser.add_argument(
        "--today",
        metavar="YYYY-MM-DD",
        help="Sobrescreve a data atual (útil para testar semanas específicas).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Envia mesmo que uma mensagem idêntica já tenha sido enviada "
             "recentemente (ignora a proteção anti-duplicação).",
    )
    return parser.parse_args(argv)


def _resolve_today(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(
            f"Valor inválido para --today: {value!r} (esperado YYYY-MM-DD)"
        ) from e


def _message_hash(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def _already_sent_recently(message: str, window_hours: int = DEDUP_WINDOW_HOURS) -> bool:
    """
    Retorna True se uma mensagem idêntica já foi enviada dentro da janela,
    evitando duplicações. Lê o arquivo de estado STATE_FILE.
    """
    if not os.path.isfile(STATE_FILE):
        return False
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        last_hash = state.get("hash")
        last_ts = datetime.fromisoformat(state["timestamp"])
    except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
        logger.warning("Não foi possível ler %s (%s). Ignorando dedupe.", STATE_FILE, e)
        return False

    if last_hash != _message_hash(message):
        return False
    elapsed = datetime.now(timezone.utc) - last_ts
    if elapsed < timedelta(hours=window_hours):
        logger.warning(
            "Mensagem idêntica já enviada há %s (< %dh). Pulando envio para "
            "evitar duplicação. Use --force para enviar mesmo assim.",
            elapsed, window_hours,
        )
        return True
    return False


def _record_sent(message: str) -> None:
    state = {
        "hash": _message_hash(message),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError as e:
        logger.warning("Não foi possível gravar %s (%s).", STATE_FILE, e)


def run(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        today_override = _resolve_today(args.today)
        settings = Settings.from_env(require_webhook=not args.dry_run)
        worksheet = SheetsClient(
            credentials_path=settings.credentials_path,
            spreadsheet_id=settings.spreadsheet_id,
            tab_name=settings.sheet_tab_name,
        ).open_worksheet()

        plantonistas = PlantaoExtractor(worksheet, today=today_override).extract()
        resolver = MentionResolver.from_path(settings.mentions_path)
        message = format_message(plantonistas, resolver)
        logger.info("Mensagem gerada:\n%s", message)

        if args.dry_run:
            logger.info("Modo --dry-run: mensagem NÃO será enviada ao Google Chat.")
            print("\n===== PREVIEW DA MENSAGEM =====")
            print(message)
            print("===== FIM DO PREVIEW =====\n")
            return 0

        if not args.force and _already_sent_recently(message):
            return 0

        ChatNotifier(settings.webhook_url).send(message)
        _record_sent(message)
        return 0

    except EnvironmentError as e:
        logger.error("Erro de configuração: %s", e)
    except ValueError as e:
        logger.error("Argumento inválido: %s", e)
    except FileNotFoundError as e:
        logger.error("Arquivo de credenciais não encontrado: %s", e)
    except gspread.exceptions.SpreadsheetNotFound:
        logger.error("Planilha não encontrada. Verifique SPREADSHEET_ID e o compartilhamento com a Service Account.")
    except gspread.exceptions.WorksheetNotFound:
        logger.error("Aba não encontrada na planilha.")
    except LookupError as e:
        logger.error("Falha na extração: %s", e)
    except requests.RequestException as e:
        logger.error("Falha ao enviar mensagem para o Google Chat: %s", e)
    except Exception:
        logger.exception("Erro inesperado.")
    return 1


if __name__ == "__main__":
    sys.exit(run())
