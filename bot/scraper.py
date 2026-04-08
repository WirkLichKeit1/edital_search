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

# Títulos de link que são considerados genéricos (não descritivos do edital)
_TITULOS_GENERICOS = {
    "download", "pdf", "clique aqui", "baixar", "abrir",
    "ver", "link", "acesse", "download pdf", "baixar pdf",
    "abrir pdf", "ver pdf",
}


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


def _extrair_contexto_link(tag) -> str:
    """
    Extrai texto de contexto do elemento pai imediato do link (li, td, p, div).

    Sobe para o avô APENAS se o pai for um elemento inline pequeno (span, b, etc.)
    sem texto próprio relevante — evita capturar o texto de contêineres grandes
    (table, ul) que agrupam dezenas de editais ao mesmo tempo.

    FIX: versão anterior subia sempre ao avô, fazendo com que um <tr> ou <ul>
    com centenas de linhas de editais fosse retornado como "contexto" de cada
    link individual — resultando em 700+ editais passando pelo filtro inicial.
    """
    pai = tag.parent
    if not pai:
        return ""

    pai_texto = pai.get_text(separator=" ", strip=True)

    # Sobe para o avô somente se o pai for inline e tiver pouco texto próprio
    _INLINE = {"span", "b", "strong", "em", "i", "u", "a", "small"}
    if pai.name in _INLINE or len(pai_texto) < 15:
        avo = pai.parent
        if avo and avo.name not in ("body", "html", "[document]", None):
            return avo.get_text(separator=" ", strip=True)

    return pai_texto


async def pegar_editais(url_editais: str, timeout: int = 30) -> list[Edital]:
    """
    Coleta todos os links de editais PDF da página.
    Retorna lista de Edital com titulo e link preenchidos e, quando disponível,
    texto_contexto anotado dinamicamente para uso posterior no pipeline de busca.

    FIX: critério de entrada agora exige "edital" no texto do próprio <a>,
    não no contexto combinado — impede que um contêiner grande (table/ul) com
    a palavra "edital" faça centenas de links PDF passarem pelo filtro.
    """
    response = await fazer_request(url_editais, timeout=timeout)
    if not response:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    editais: list[Edital] = []
    links_vistos: set[str] = set()

    for link in soup.find_all("a", href=True):
        titulo: str = link.get_text(strip=True)
        href: str = link["href"]

        # Só interessa links para PDF
        if ".pdf" not in href.lower():
            continue

        # FIX: exige "edital" no texto do próprio <a>, não no contexto combinado.
        # O contexto serve para enriquecer cidade/termos, não para liberar a entrada.
        if "edital" not in titulo.lower():
            continue

        if not href.startswith("http"):
            from urllib.parse import urljoin
            href = urljoin(url_editais, href)

        # Evita duplicatas (mesmo PDF linkado em lugares diferentes da página)
        if href in links_vistos:
            continue
        links_vistos.add(href)

        # Captura texto ao redor do link na página para auxiliar filtragem
        contexto = _extrair_contexto_link(link)

        # Prefere o título do <a> quando descritivo; usa contexto como fallback
        titulo_lower = titulo.strip().lower()
        eh_generico = (
            len(titulo) < 20
            or titulo_lower in _TITULOS_GENERICOS
        )
        titulo_final = contexto[:150].strip() if eh_generico else titulo

        edital = Edital(titulo=titulo_final, link=href)
        edital.texto_contexto = contexto  # type: ignore[attr-defined]
        editais.append(edital)

    logger.info("%d edital(is) encontrado(s) no site.", len(editais))
    return editais


# ─────────────────────────────────────────────
# Extração de texto do PDF
# ─────────────────────────────────────────────


async def extrair_texto_pdf(url_pdf: str, timeout: int = 30) -> Optional[str]:
    """
    Baixa o PDF e extrai o texto de todas as páginas.
    Retorna None se o download ou a extração falhar.

    FIX: limite de tamanho de download (10 MB) para evitar que PDFs
    gigantes travem o bot ou estourem memória.
    """
    response = await fazer_request(url_pdf, timeout=timeout, stream=True)
    if not response:
        return None

    # FIX: rejeita PDFs muito grandes antes de tentar processar
    _MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB
    content = response.content
    if len(content) > _MAX_PDF_BYTES:
        logger.warning(
            "PDF ignorado por exceder limite de tamanho (%d MB): %s",
            len(content) // (1024 * 1024),
            url_pdf,
        )
        return None

    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
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
    progresso_cb: Optional[Callable] = None,
) -> dict:
    """
    Faz scraping completo, filtra por cidade e termos de TI,
    persiste novos editais no banco e retorna um resumo da operação.

    Pipeline de filtragem (em ordem):
      1. Filtro de cidade  — título do link OU texto de contexto da página
      2. Filtro por termos — título do link OU texto de contexto da página
      3. Análise do PDF    — conteúdo completo do arquivo PDF

    progresso_cb: callable(str) opcional — aceita tanto funções síncronas
    quanto coroutines assíncronas (para uso direto no event loop do bot).
    """
    import inspect

    async def log(msg: str) -> None:
        logger.info(msg)
        if progresso_cb:
            result = progresso_cb(msg)
            if inspect.isawaitable(result):
                await result

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

    # FIX: captura os links conhecidos UMA vez antes do loop — evita
    # que um edital aceito numa iteração anterior seja reprocessado
    # na mesma busca se pegar_editais retornar duplicatas residuais.
    links_conhecidos = user.links_conhecidos()

    await log("📄 Coletando editais da página...")
    editais_site = await pegar_editais(url_editais, timeout=timeout)
    resultado["total_site"] = len(editais_site)

    if not editais_site:
        await log("⚠️ Nenhum edital encontrado na página.")
        salvar_user(chat_id, user)
        return resultado

    await log(f"📋 {len(editais_site)} edital(is) encontrado(s). Analisando...")

    cidade = user.config.cidade
    termos = user.config.termos

    for i, edital in enumerate(editais_site, 1):
        if edital.link in links_conhecidos:
            resultado["ja_conhecidos"] += 1
            continue

        # Texto de contexto capturado da página (pode não existir em editais vindos do banco)
        contexto_pagina: str = getattr(edital, "texto_contexto", "")

        await log(f"\n🔎 [{i}/{len(editais_site)}] {edital.titulo}")

        # ── 1. Filtro de cidade ──────────────────────────────────────────────
        cidade_no_titulo   = edital_eh_cidade(edital.titulo, cidade)
        cidade_no_contexto = edital_eh_cidade(contexto_pagina, cidade) if contexto_pagina else False

        if not cidade_no_titulo and not cidade_no_contexto:
            motivo = f"cidade (não contém '{cidade}')"
            await log(f"  ⏭ Ignorado — {motivo}")
            edital.motivo = motivo
            edital.rejeitado_em = datetime.now().isoformat()
            user.rejeitados.append(edital)
            resultado["novos_rejeitados"].append(edital)
            # FIX: adiciona ao set local para não reprocessar na mesma execução
            links_conhecidos.add(edital.link)
            continue

        if not cidade_no_titulo and cidade_no_contexto:
            await log("  📍 Cidade encontrada no contexto da página (não no título do PDF)")

        # ── 2. Filtro por termos no título ou contexto da página ─────────────
        texto_pagina = f"{edital.titulo} {contexto_pagina}"
        achados = termos_encontrados(texto_pagina, termos)
        if achados:
            fonte = "título" if termos_encontrados(edital.titulo, termos) else "contexto da página"
            await log(f"  ✅ Aceito pelo {fonte} — termos: {', '.join(achados)}")
            edital.aceito_em = datetime.now().isoformat()
            edital.encontrado_em = "titulo"
            edital.termos_ti = achados
            user.aceitos.append(edital)
            resultado["novos_aceitos"].append(edital)
            links_conhecidos.add(edital.link)
            continue

        # ── 3. Análise do PDF ────────────────────────────────────────────────
        await log("  📥 Título e contexto genéricos. Baixando PDF para análise...")
        resultado["pdfs_baixados"] += 1
        user.stats.total_pdfs_baixados += 1

        texto_pdf = await extrair_texto_pdf(edital.link, timeout=timeout)

        if texto_pdf is None:
            msg = f"Não foi possível analisar o PDF: {edital.link}"
            await log(f"  ⚠️ {msg}")
            resultado["erros"].append(msg)
            # FIX: edital sem PDF analisável é rejeitado e persiste,
            # evitando que seja tentado novamente em toda busca futura.
            edital.motivo = "PDF inacessível ou ilegível"
            edital.rejeitado_em = datetime.now().isoformat()
            user.rejeitados.append(edital)
            resultado["novos_rejeitados"].append(edital)
            links_conhecidos.add(edital.link)
            continue

        achados = termos_encontrados(texto_pdf, termos)
        if achados:
            await log(f"  ✅ Aceito pelo PDF — termos: {', '.join(achados[:3])}")
            edital.aceito_em = datetime.now().isoformat()
            edital.encontrado_em = "pdf"
            edital.termos_ti = achados
            user.aceitos.append(edital)
            resultado["novos_aceitos"].append(edital)
        else:
            motivo = "sem termos de TI (título, contexto da página e PDF verificados)"
            await log(f"  ❌ Rejeitado — {motivo}")
            edital.motivo = motivo
            edital.rejeitado_em = datetime.now().isoformat()
            user.rejeitados.append(edital)
            resultado["novos_rejeitados"].append(edital)

        links_conhecidos.add(edital.link)

    salvar_user(chat_id, user)

    await log(
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

    FIX: itera sobre uma cópia da lista para evitar modificar a lista
    enquanto itera sobre ela (RuntimeError: list changed size during iteration).
    """
    promovidos: list[Edital] = []
    candidatos = [e for e in user.rejeitados if e.motivo and "cidade" not in e.motivo]

    for edital in candidatos:  # candidatos já é uma cópia — seguro iterar
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
