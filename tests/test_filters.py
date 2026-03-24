"""
tests/test_filters.py
Testes unitários para bot/filters.py.
Funções puras — sem mocks, sem I/O.
"""

import pytest
from bot.filters import normalizar, edital_eh_cidade, termos_encontrados


# ─────────────────────────────────────────────
# normalizar
# ─────────────────────────────────────────────

class TestNormalizar:
    def test_remove_acentos(self):
        assert normalizar("Ação") == "acao"

    def test_converte_para_minusculas(self):
        assert normalizar("INFORMATICA") == "informatica"

    def test_acento_e_maiuscula_juntos(self):
        assert normalizar("Ção") == "cao"

    def test_string_vazia(self):
        assert normalizar("") == ""

    def test_sem_alteracao(self):
        assert normalizar("cabo") == "cabo"


# ─────────────────────────────────────────────
# edital_eh_cidade
# ─────────────────────────────────────────────

class TestEditalEhCidade:
    def test_cidade_presente_no_titulo(self):
        assert edital_eh_cidade("Edital 001/2024 - Cabo de Santo Agostinho", "cabo") is True

    def test_cidade_ausente_no_titulo(self):
        assert edital_eh_cidade("Edital 002/2024 - Recife", "cabo") is False

    def test_case_insensitive_titulo(self):
        assert edital_eh_cidade("EDITAL CABO TI 2024", "cabo") is True

    def test_case_insensitive_cidade(self):
        assert edital_eh_cidade("Edital Cabo 2024", "CABO") is True

    def test_cidade_com_acento(self):
        assert edital_eh_cidade("Edital Recife TI", "recife") is True

    def test_cidade_normalizada(self):
        # Cidade configurada com acento, título sem
        assert edital_eh_cidade("Edital Sao Paulo TI", "são paulo") is True

    def test_titulo_vazio(self):
        assert edital_eh_cidade("", "cabo") is False

    def test_cidade_vazia(self):
        # Cidade vazia bate em qualquer título
        assert edital_eh_cidade("Edital qualquer", "") is True


# ─────────────────────────────────────────────
# termos_encontrados
# ─────────────────────────────────────────────

TERMOS_PADRAO = [
    "informatica",
    "desenvolvimento de sistemas",
    "redes de computadores",
    "banco de dados",
    "ti",
]

class TestTermosEncontrados:
    def test_termo_presente(self):
        resultado = termos_encontrados("Técnico em Informática", TERMOS_PADRAO)
        assert "informatica" in resultado

    def test_multiplos_termos(self):
        texto = "Curso de Informatica e Banco de Dados"
        resultado = termos_encontrados(texto, TERMOS_PADRAO)
        assert "informatica" in resultado
        assert "banco de dados" in resultado

    def test_nenhum_termo(self):
        resultado = termos_encontrados("Edital de Gastronomia 2024", TERMOS_PADRAO)
        assert resultado == []

    def test_case_insensitive(self):
        resultado = termos_encontrados("INFORMATICA AVANCADA", TERMOS_PADRAO)
        assert "informatica" in resultado

    def test_termo_com_acento_no_texto(self):
        resultado = termos_encontrados("Técnico em Informática para Internet", TERMOS_PADRAO)
        assert "informatica" in resultado

    def test_termo_composto(self):
        resultado = termos_encontrados(
            "Edital para Desenvolvimento de Sistemas 2024", TERMOS_PADRAO
        )
        assert "desenvolvimento de sistemas" in resultado

    def test_texto_vazio(self):
        assert termos_encontrados("", TERMOS_PADRAO) == []

    def test_lista_termos_vazia(self):
        assert termos_encontrados("Informatica", []) == []

    def test_retorna_apenas_encontrados(self):
        resultado = termos_encontrados("Curso de Informatica", TERMOS_PADRAO)
        assert "redes de computadores" not in resultado
        assert "banco de dados" not in resultado