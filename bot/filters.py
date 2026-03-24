"""
bot/filters.py
Funções puras de filtragem de editais.
Sem dependências de I/O — fácil de testar unitariamente.
"""

from __future__ import annotations

from unidecode import unidecode


# ─────────────────────────────────────────────
# Normalização
# ─────────────────────────────────────────────


def normalizar(texto: str) -> str:
    """Remove acentos e converte para minúsculas."""
    return unidecode(texto.lower())


# ─────────────────────────────────────────────
# Filtros
# ─────────────────────────────────────────────


def edital_eh_cidade(titulo: str, cidade: str) -> bool:
    """Retorna True se o título do edital contém a cidade configurada."""
    return normalizar(cidade) in normalizar(titulo)


def termos_encontrados(texto: str, termos: list[str]) -> list[str]:
    """
    Retorna quais termos da lista aparecem no texto.
    Tanto o texto quanto os termos são normalizados antes da comparação.
    """
    t = normalizar(texto)
    return [termo for termo in termos if normalizar(termo) in t]