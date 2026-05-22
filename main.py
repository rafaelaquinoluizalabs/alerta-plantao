"""
Automação de aviso semanal de plantonistas no Google Chat.

Lê a aba 'HOJE' de uma planilha Google Sheets, identifica a semana
corrente com base na data de execução, monta uma mensagem com os
plantonistas e a publica em um Incoming Webhook do Google Chat.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

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
    def _cell(row: List[str], col_1based: int) -> str:
        idx = col_1based - 1
        if 0 <= idx < len(row):
            return row[idx].strip()
        return ""

    def _log_week_range(self, current_row: List[str], next_row: List[str]) -> None:
        """
        Loga os dias (C..I) da semana corrente e o sábado que ser\u00e1
        considerado (próxima linha). Emite WARNING se a estrutura n\u00e3o
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

        # Sábado deve vir da próxima semana (linha + 1), primeira coluna preenchida
        if next_row:
            nxt_str = " ".join(f"{d:02d}" if d else "--" for d in nxt_days)
            logger.info("Pr\u00f3xima semana (sábado virá daqui): %s", nxt_str)
            first_next = next((d for d in nxt_days if d is not None), None)
            last_cur = next((d for d in reversed(cur_days) if d is not None), None)
            if first_next is not None and last_cur is not None:
                # Em uma estrutura Seg..Dom, last_cur (domingo) + 1 == first_next (segunda)
                # Aceitamos virada de mês ignorando a checagem nesse caso.
                if first_next != last_cur + 1 and first_next != 1:
                    logger.warning(
                        "Salto inesperado entre semanas: \u00faltimo dia da semana "
                        "atual=%s e primeiro da pr\u00f3xima=%s. Confira o layout "
                        "da planilha (esperado Seg..Dom cont\u00edguo).",
                        last_cur, first_next,
                    )
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
        """
        target_day = self._today.day
        target_month_name = MONTH_NAMES_PT[self._today.month]
        start_idx = DATA_START_ROW - 1  # 0-based
        current_month_matches = False

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
                if day == target_day:
                    logger.debug(
                        "Match: linha=%d coluna=%d valor='%s'",
                        row_idx + 1, col_1based, value,
                    )
                    return row_idx
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
# Formatação
# ---------------------------------------------------------------------------
def format_message(p: Plantonistas) -> str:
    return (
        "-------\n"
        "💙 Plantão da Semana 💙\n"
        "\n"
        f"SL:  {p.squad_lead}\n"
        f"Cloud: {p.cloud}\n"
        f"Onprem: {p.onprem}\n"
        f"Dados:  {p.dados}\n"
        f"TL iPET:   {p.tl}\n"
        "\n"
        "💙 Plantão do sábado:\n"
        "\n"
        f"Cloud:   {p.cloud_sabado}\n"
        f"Onprem:  {p.onprem_sabado}\n"
        f"Dados:  {p.dados_sabado}\n"
        "------"
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
        message = format_message(plantonistas)
        logger.info("Mensagem gerada:\n%s", message)

        if args.dry_run:
            logger.info("Modo --dry-run: mensagem NÃO será enviada ao Google Chat.")
            print("\n===== PREVIEW DA MENSAGEM =====")
            print(message)
            print("===== FIM DO PREVIEW =====\n")
            return 0

        ChatNotifier(settings.webhook_url).send(message)
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
