"""
tests/test_scraper.py
Testes para bot/scraper.py.
Todo I/O de rede é mockado — os testes rodam offline.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from bot.database import Edital, UserConfig, UserData, UserStats
from bot.scraper import (
    checar_site,
    pegar_editais,
    extrair_texto_pdf,
    buscar_novos_editais,
    reanalisar_rejeitados,
)


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def user_cabo() -> UserData:
    return UserData(
        config=UserConfig(
            cidade="cabo",
            termos=["informatica", "desenvolvimento de sistemas"],
        ),
        stats=UserStats(),
    )


def _mock_response(status_code: int = 200, text: str = "", content: bytes = b"") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    r.content = content
    r.raise_for_status = MagicMock()
    return r


HTML_COM_EDITAIS = """
<html><body>
  <a href="edital_cabo_ti_001.pdf">Edital 001 - Cabo Informatica 2024</a>
  <a href="edital_cabo_gas_002.pdf">Edital 002 - Cabo Gastronomia 2024</a>
  <a href="edital_recife_003.pdf">Edital 003 - Recife TI 2024</a>
  <a href="nao_e_edital.html">Link sem PDF</a>
</body></html>
"""


# ─────────────────────────────────────────────
# checar_site
# ─────────────────────────────────────────────

class TestChecarSite:
    @pytest.mark.asyncio
    async def test_site_online(self):
        mock_r = _mock_response(status_code=200)
        with patch("bot.scraper.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_r)
            online, msg = await checar_site("https://exemplo.com")
        assert online is True
        assert "200" in msg

    @pytest.mark.asyncio
    async def test_site_erro_servidor(self):
        mock_r = _mock_response(status_code=500)
        with patch("bot.scraper.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_r)
            online, msg = await checar_site("https://exemplo.com")
        assert online is False
        assert "500" in msg

    @pytest.mark.asyncio
    async def test_site_timeout(self):
        import httpx
        with patch("bot.scraper.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.ConnectTimeout("timeout")
            )
            online, msg = await checar_site("https://exemplo.com")
        assert online is False
        assert "Timeout" in msg

    @pytest.mark.asyncio
    async def test_site_sem_conexao(self):
        import httpx
        with patch("bot.scraper.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.ConnectError("refused")
            )
            online, msg = await checar_site("https://exemplo.com")
        assert online is False
        assert "conexão" in msg.lower()


# ─────────────────────────────────────────────
# pegar_editais
# ─────────────────────────────────────────────

class TestPegarEditais:
    @pytest.mark.asyncio
    async def test_encontra_links_pdf(self):
        with patch("bot.scraper.fazer_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(text=HTML_COM_EDITAIS)
            editais = await pegar_editais("https://exemplo.com/editais/")

        titulos = [e.titulo for e in editais]
        assert any("Cabo Informatica" in t for t in titulos)
        assert any("Gastronomia" in t for t in titulos)
        assert any("Recife" in t for t in titulos)

    @pytest.mark.asyncio
    async def test_ignora_links_sem_pdf(self):
        with patch("bot.scraper.fazer_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = _mock_response(text=HTML_COM_EDITAIS)
            editais = await pegar_editais("https://exemplo.com/editais/")

        links = [e.link for e in editais]
        assert not any(".html" in l for l in links)

    @pytest.mark.asyncio
    async def test_retorna_vazio_se_request_falhar(self):
        with patch("bot.scraper.fazer_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = None
            editais = await pegar_editais("https://exemplo.com/editais/")
        assert editais == []


# ─────────────────────────────────────────────
# extrair_texto_pdf
# ─────────────────────────────────────────────

class TestExtrairTextoPdf:
    @pytest.mark.asyncio
    async def test_retorna_none_se_request_falhar(self):
        with patch("bot.scraper.fazer_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = None
            resultado = await extrair_texto_pdf("https://exemplo.com/edital.pdf")
        assert resultado is None

    @pytest.mark.asyncio
    async def test_extrai_texto_do_pdf(self, tmp_path):
        # Cria um PDF real mínimo com pypdf
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        pdf_path = tmp_path / "teste.pdf"
        with open(pdf_path, "wb") as f:
            writer.write(f)

        pdf_bytes = pdf_path.read_bytes()
        mock_r = _mock_response(content=pdf_bytes)

        with patch("bot.scraper.fazer_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_r
            resultado = await extrair_texto_pdf("https://exemplo.com/edital.pdf")

        # Página em branco — texto vazio mas sem erro
        assert resultado is not None
        assert isinstance(resultado, str)


# ─────────────────────────────────────────────
# buscar_novos_editais
# ─────────────────────────────────────────────

class TestBuscarNovosEditais:
    @pytest.mark.asyncio
    async def test_aceita_edital_por_titulo(self, user_cabo, tmp_path, monkeypatch):
        import bot.database as db_module
        monkeypatch.setattr(db_module, "ARQUIVO_DB", tmp_path / "db.json")

        from bot.database import get_user
        get_user(123, user_cabo.config)

        editais_mock = [
            Edital(titulo="Edital 001 - Cabo Informatica", link="https://ex.com/1.pdf"),
        ]
        with patch("bot.scraper.pegar_editais", new_callable=AsyncMock, return_value=editais_mock):
            resultado = await buscar_novos_editais(
                chat_id=123,
                user=user_cabo,
                url_editais="https://exemplo.com/editais/",
            )

        assert len(resultado["novos_aceitos"]) == 1
        assert resultado["novos_aceitos"][0].encontrado_em == "titulo"

    @pytest.mark.asyncio
    async def test_rejeita_edital_por_cidade(self, user_cabo, tmp_path, monkeypatch):
        import bot.database as db_module
        monkeypatch.setattr(db_module, "ARQUIVO_DB", tmp_path / "db.json")

        from bot.database import get_user
        get_user(123, user_cabo.config)

        editais_mock = [
            Edital(titulo="Edital 001 - Recife Informatica", link="https://ex.com/2.pdf"),
        ]
        with patch("bot.scraper.pegar_editais", new_callable=AsyncMock, return_value=editais_mock):
            resultado = await buscar_novos_editais(
                chat_id=123,
                user=user_cabo,
                url_editais="https://exemplo.com/editais/",
            )

        assert len(resultado["novos_aceitos"]) == 0
        assert len(resultado["novos_rejeitados"]) == 1
        assert "cidade" in resultado["novos_rejeitados"][0].motivo

    @pytest.mark.asyncio
    async def test_aceita_edital_por_pdf(self, user_cabo, tmp_path, monkeypatch):
        import bot.database as db_module
        monkeypatch.setattr(db_module, "ARQUIVO_DB", tmp_path / "db.json")

        from bot.database import get_user
        get_user(123, user_cabo.config)

        editais_mock = [
            Edital(titulo="Edital 001 - Cabo Curso Tecnico", link="https://ex.com/3.pdf"),
        ]
        with (
            patch("bot.scraper.pegar_editais", new_callable=AsyncMock, return_value=editais_mock),
            patch("bot.scraper.extrair_texto_pdf", new_callable=AsyncMock,
                  return_value="curso de informatica para internet 2024"),
        ):
            resultado = await buscar_novos_editais(
                chat_id=123,
                user=user_cabo,
                url_editais="https://exemplo.com/editais/",
            )

        assert len(resultado["novos_aceitos"]) == 1
        assert resultado["novos_aceitos"][0].encontrado_em == "pdf"

    @pytest.mark.asyncio
    async def test_ignora_ja_conhecidos(self, user_cabo, tmp_path, monkeypatch):
        import bot.database as db_module
        monkeypatch.setattr(db_module, "ARQUIVO_DB", tmp_path / "db.json")

        link_existente = "https://ex.com/1.pdf"
        user_cabo.aceitos.append(Edital(
            titulo="Edital já visto",
            link=link_existente,
            aceito_em=datetime.now().isoformat(),
        ))

        from bot.database import get_user, salvar_user
        get_user(123, user_cabo.config)
        salvar_user(123, user_cabo)

        editais_mock = [Edital(titulo="Edital 001 - Cabo TI", link=link_existente)]
        with patch("bot.scraper.pegar_editais", new_callable=AsyncMock, return_value=editais_mock):
            resultado = await buscar_novos_editais(
                chat_id=123,
                user=user_cabo,
                url_editais="https://exemplo.com/editais/",
            )

        assert resultado["ja_conhecidos"] == 1
        assert resultado["novos_aceitos"] == []


# ─────────────────────────────────────────────
# reanalisar_rejeitados
# ─────────────────────────────────────────────

class TestReanalisarRejeitados:
    @pytest.mark.asyncio
    async def test_promove_com_novo_termo(self, user_cabo, tmp_path, monkeypatch):
        import bot.database as db_module
        monkeypatch.setattr(db_module, "ARQUIVO_DB", tmp_path / "db.json")

        from bot.database import get_user, salvar_user
        get_user(123, user_cabo.config)

        edital = Edital(
            titulo="Edital 001 - Cabo Machine Learning",
            link="https://ex.com/ml.pdf",
            motivo="sem termos de TI (título e PDF verificados)",
            rejeitado_em=datetime.now().isoformat(),
        )
        user_cabo.rejeitados.append(edital)
        user_cabo.config.termos.append("machine learning")
        salvar_user(123, user_cabo)

        resultado = await reanalisar_rejeitados(123, user_cabo)
        assert len(resultado["promovidos"]) == 1
        assert resultado["promovidos"][0].encontrado_em == "reanalise_titulo"

    @pytest.mark.asyncio
    async def test_nao_promove_rejeitado_por_cidade(self, user_cabo, tmp_path, monkeypatch):
        import bot.database as db_module
        monkeypatch.setattr(db_module, "ARQUIVO_DB", tmp_path / "db.json")

        from bot.database import get_user, salvar_user
        get_user(123, user_cabo.config)

        edital = Edital(
            titulo="Edital 001 - Recife Informatica",
            link="https://ex.com/rec.pdf",
            motivo="cidade (não contém 'cabo')",
            rejeitado_em=datetime.now().isoformat(),
        )
        user_cabo.rejeitados.append(edital)
        salvar_user(123, user_cabo)

        resultado = await reanalisar_rejeitados(123, user_cabo)
        assert resultado["promovidos"] == []