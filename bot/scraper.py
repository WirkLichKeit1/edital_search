"""
bot/scraper.py
Scraping da página de editais e extração de texto de PDFs.
Usa httpx com AsyncClient para I/O assíncrono nativo.
Retry declarativo via tenacity.
"""

from __future__ import annotations

import logging
import tempfile
import os
from datetime import datetime
from typing import Callable, Optional

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from bot.database import Edital, UserData, salvar_user
from bot.filters import edital_eh_cidade, termos_encontrados

logger = logging.getLogger(__name__)

# Cabeçalho padrão para todas as requisições
_HEADERS = {"User-Agent": "Mozilla/5.0 (SENAI-Bot/3.0)"}


# ─────────────────────────────────────────────
# HTTP — ping e fetch com retry
# ─────────────────────────────────────────────


async def checar_site(url: str, timeout: int = 10) -> tuple[bool, str]:
    """
    Ping rápido para verificar se o site está acessível.
    Retorna (online, mensagem_descritiva).
    """
    try:
        async with httpx.AsyncClient(verify=False, headers=_HEADERS) as client:
            r = await client.get(url, timeout=timeout)
        if r.status_code < 500:
            return True, f"Online ✅ (HTTP {r.status_code})"
        return False, f"Erro no servidor ❌ (HTTP {r.status_code})"
    except httpx.ConnectTimeout:
        return False, "Timeout de conexão ❌"
    except httpx.ConnectError:
        return False, "Sem conexão com o servidor ❌"
    except Exception as exc:
        return False, f"Erro desconhecido ❌: {exc}"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(httpx.HTTPError),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def _fetch(url: str, timeout: int, stream: bool = False) -> httpx.Response:
    """GET com retry exponencial. Levanta exceção após 3 tentativas."""
    async with httpx.AsyncClient(
        verify=False,
        headers=_HEADERS,
        follow_redirects=True,
    ) as client:
        if stream:
            # Para PDFs, retornamos o conteúdo binário direto
            r = await client.get(url, timeout=timeout)
            r.raise_for_status()
            return r
        r = await client.get(url, timeout=timeout)
        r.raise_for_status()
        return r


async def fazer_request(
    url: str,
    timeout: int = 30,
    stream: bool = False,
) -> Optional[httpx.Response]:
    """
    Wrapper seguro sobre _fetch. Retorna None em caso de falha definitiva,
    em vez de propagar exceção — compatível com o fluxo do scraper.
    """
    try:
        return await _fetch(url, timeout=timeout, stream=stream)
    except Exception as exc:
        logger.error("Todas as tentativas falharam para %s: %s", url, exc)
        return None


# ─────────────────────────────────────────────
# Scraping da página de editais
# ─────────────────────────────────────────────


async def pegar_editais(url_editais: str, timeout: int = 30) -> list[Edital]:
    """
    Coleta todos os links de editais PDF da página.
    Retorna lista de Edital com titulo e link preenchidos.
    """
    response = await fazer_request(url_editais, timeout=timeout)
    if not response:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    editais: list[Edital] = []

    for link in soup.find_all("a", href=True):
        titulo: str = link.get_text(strip=True)
        href: str = link["href"]

        if titulo.lower().startswith("edital") and ".pdf" in href.lower():
            if not href.startswith("http"):
                href = str(httpx.URL(url_editais).copy_with(path=href))
            editais.append(Edital(titulo=titulo, link=href))

    logger.info("%d edital(is) encontrado(s) no site.", len(editais))
    return editais


# ─────────────────────────────────────────────
# Extração de texto do PDF
# ─────────────────────────────────────────────


async def extrair_texto_pdf(url_pdf: str, timeout: int = 30) -> Optional[str]:
    """
    Baixa o PDF e extrai o texto de todas as páginas.
    Retorna None se o download ou a extração falhar.
    """
    response = await fazer_request(url_pdf, timeout=timeout, stream=True)
    if not response:
        return None

    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(response.content)
            tmp_path = tmp.name

        reader = PdfReader(tmp_path)
        texto = " ".join((p.extract_text() or "") for p in reader.pages)
        logger.info("  PDF: %d página(s), %d chars extraídos", len(reader.pages), len(texto))
        return texto

    except Exception as exc:
        logger.error("Erro ao processar PDF %s: %s", url_pdf, exc)
        return None

    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ─────────────────────────────────────────────
# Busca completa
# ─────────────────────────────────────────────


async def buscar_novos_editais(
    chat_id: int | str,
    user: UserData,
    url_editais: str,
    timeout: int = 30,
    progresso_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Faz scraping completo, filtra por cidade e termos de TI,
    persiste novos editais no banco e retorna um resumo da operação.

    progresso_cb: callable(str) opcional para logs em tempo real no Telegram.
    """
    def log(msg: str) -> None:
        logger.info(msg)
        if progresso_cb:
            progresso_cb(msg)

    resultado = {
        "novos_aceitos": [],
        "novos_rejeitados": [],
        "erros": [],
        "total_site": 0,
        "ja_conhecidos": 0,
        "pdfs_baixados": 0,
    }

    # Atualiza stats
    user.stats.total_buscas += 1
    user.ultima_busca_completa = datetime.now().isoformat()

    links_conhecidos = user.links_conhecidos()

    log("📄 Coletando editais da página...")
    editais_site = await pegar_editais(url_editais, timeout=timeout)
    resultado["total_site"] = len(editais_site)

    if not editais_site:
        log("⚠️ Nenhum edital encontrado na página.")
        salvar_user(chat_id, user)
        return resultado

    log(f"📋 {len(editais_site)} edital(is) encontrado(s). Analisando...")

    cidade = user.config.cidade
    termos = user.config.termos

    for i, edital in enumerate(editais_site, 1):
        if edital.link in links_conhecidos:
            resultado["ja_conhecidos"] += 1
            continue

        log(f"\n🔎 [{i}/{len(editais_site)}] {edital.titulo}")

        # 1) Filtro de cidade
        if not edital_eh_cidade(edital.titulo, cidade):
            motivo = f"cidade (não contém '{cidade}')"
            log(f"  ⏭ Ignorado — {motivo}")
            edital.motivo = motivo
            edital.rejeitado_em = datetime.now().isoformat()
            user.rejeitados.append(edital)
            resultado["novos_rejeitados"].append(edital)
            continue

        # 2) Título menciona algum termo?
        achados = termos_encontrados(edital.titulo, termos)
        if achados:
            log(f"  ✅ Aceito pelo título — termos: {', '.join(achados)}")
            edital.aceito_em = datetime.now().isoformat()
            edital.encontrado_em = "titulo"
            edital.termos_ti = achados
            user.aceitos.append(edital)
            resultado["novos_aceitos"].append(edital)
            continue

        # 3) Verificar conteúdo do PDF
        log("  📥 Título genérico. Baixando PDF para análise...")
        resultado["pdfs_baixados"] += 1
        user.stats.total_pdfs_baixados += 1

        texto_pdf = await extrair_texto_pdf(edital.link, timeout=timeout)

        if texto_pdf is None:
            msg = f"Não foi possível analisar o PDF: {edital.link}"
            log(f"  ⚠️ {msg}")
            resultado["erros"].append(msg)
            continue

        achados = termos_encontrados(texto_pdf, termos)
        if achados:
            log(f"  ✅ Aceito pelo PDF — termos: {', '.join(achados[:3])}")
            edital.aceito_em = datetime.now().isoformat()
            edital.encontrado_em = "pdf"
            edital.termos_ti = achados
            user.aceitos.append(edital)
            resultado["novos_aceitos"].append(edital)
        else:
            motivo = "sem termos de TI (título e PDF verificados)"
            log(f"  ❌ Rejeitado — {motivo}")
            edital.motivo = motivo
            edital.rejeitado_em = datetime.now().isoformat()
            user.rejeitados.append(edital)
            resultado["novos_rejeitados"].append(edital)

    salvar_user(chat_id, user)

    log(
        f"\n📊 Concluído — ✅ {len(resultado['novos_aceitos'])} aceito(s), "
        f"❌ {len(resultado['novos_rejeitados'])} rejeitado(s), "
        f"📥 {resultado['pdfs_baixados']} PDF(s), "
        f"🔁 {resultado['ja_conhecidos']} já conhecidos."
    )
    return resultado


# ─────────────────────────────────────────────
# Re-análise de rejeitados
# ─────────────────────────────────────────────


async def reanalisar_rejeitados(chat_id: int | str, user: UserData) -> dict:
    """
    Re-analisa editais rejeitados por conteúdo (não por cidade)
    usando a lista de termos atual do usuário.
    Útil após o usuário adicionar novos termos via /addtermo.
    """
    promovidos: list[Edital] = []
    candidatos = [e for e in user.rejeitados if e.motivo and "cidade" not in e.motivo]

    for edital in candidatos:
        achados = termos_encontrados(edital.titulo, user.config.termos)
        if achados:
            edital.aceito_em = datetime.now().isoformat()
            edital.encontrado_em = "reanalise_titulo"
            edital.termos_ti = achados
            edital.motivo = None
            edital.rejeitado_em = None
            user.aceitos.append(edital)
            user.rejeitados.remove(edital)
            promovidos.append(edital)

    if promovidos:
        salvar_user(chat_id, user)

    return {
        "promovidos": promovidos,
        "mantidos": len(candidatos) - len(promovidos),
    }