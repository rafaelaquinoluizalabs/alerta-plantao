"""
Mapa de nome (como aparece na planilha) -> username no VictorOps / Splunk On-Call.

Preencha cada entrada com o nome EXATO que aparece na célula da planilha à
esquerda e o ``username`` correspondente no VictorOps à direita. A busca é
tolerante a acentos e maiúsculas/minúsculas (veja ``resolver_username``).

Para descobrir os usernames disponíveis, rode:

    .venv/bin/python tools/ajustar_plantonistas.py
"""

from __future__ import annotations

import unicodedata
from typing import Dict, Optional

# nome-na-planilha -> username-no-victorops
NOME_VICTOROPS_MAP: Dict[str, str] = {
    "Nery": "neryresende",
    "Emerson": "emerson.forster",
    "Thiago Medeiros": "thiago.reis",
    "PC": "paulo.cferreira",
    # Demais plantonistas que aparecem na planilha ao longo das semanas:
    # "Rafael Aquino": "raf_aquino",
    # "André": "andre.ferreira",
    # "Cintia": "cintia.fsantos",
    # "Ailton": "ailton.oliveira",
}


def _normalizar(nome: str) -> str:
    """Remove acentos, espaços extras e normaliza para minúsculas."""
    sem_acento = "".join(
        c
        for c in unicodedata.normalize("NFD", nome)
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(sem_acento.split()).lower()


# Mapa normalizado (chave canonicalizada -> username), montado uma única vez.
_NORM_MAP: Dict[str, str] = {
    _normalizar(k): v for k, v in NOME_VICTOROPS_MAP.items() if k
}


def resolver_username(nome: str) -> Optional[str]:
    """
    Resolve o ``username`` do VictorOps a partir do nome da planilha,
    ignorando acentos e capitalização. Retorna ``None`` se não houver mapeamento.
    """
    if not nome:
        return None
    return _NORM_MAP.get(_normalizar(nome))
