"""
Bot SENAI Editais - Versão Inteligente
Monitora editais do SENAI-PE filtrando por cidade e área de TI.

Lógica do /auto (job unificado):
  - A cada 5min: checa se o site está online (leve, sem scraping)
  - Quando o site VOLTA: notifica imediatamente e dispara busca completa
  - Quando o site CAI: notifica imediatamente
  - A cada 24h: busca completa de editais (só se o site estiver online)
"""

import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests
import urllib3
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pypdf import PdfReader
from unidecode import unidecode

from flask import Flask, jsonify
from telegram import Update, BotCommand
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
import threading
import asyncio

# ─────────────────────────────────────────────
# Configuração inicial
# ─────────────────────────────────────────────

load_dotenv()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Configurações
# ─────────────────────────────────────────────

URL_EDITAIS          = "https://www.pe.senai.br/editais/"
URL_PORTAL           = "https://sge.pe.senai.br"
ARQUIVO_DB           = Path("editais_cabo_ti.json")
TOKEN                = os.getenv("BOT_TOKEN")
CIDADE_ALVO          = "cabo"
INTERVALO_MONITOR    = 300        # 5min — ping leve para saber se o site voltou
INTERVALO_BUSCA      = 86_400     # 24h — busca completa de editais
REQUEST_TIMEOUT      = 30
MAX_RETRIES          = 3

TI_TERMOS: list[str] = [
    "desenvolvimento de sistemas",
    "tecnico em desenvolvimento de sistemas",
    "informatica",
    "tecnico em informatica",
    "informatica para internet",
    "redes de computadores",
    "tecnico em redes",
    "programacao",
    "programador",
    "desenvolvimento web",
    "software",
    "banco de dados",
    "seguranca da informacao",
    "ciberseguranca",
    "tecnologia da informacao",
    "ti",
    "suporte tecnico",
    "manutencao de computadores",
    "analise e desenvolvimento",
    "ads",
]

# ─────────────────────────────────────────────
# Persistência
# ─────────────────────────────────────────────

def carregar_db() -> dict:
    if ARQUIVO_DB.exists():
        try:
            return json.loads(ARQUIVO_DB.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("DB corrompido. Criando novo.")
    return {
        "aceitos": [],
        "rejeitados": [],
        "ultima_busca": None,
        "site_online": None,
        "site_offline_desde": None,
        "historico_disponibilidade": [],
        "total_buscas": 0,
        "total_pdfs_baixados": 0,
        "ultima_busca_completa": None,
    }


def salvar_db(db: dict) -> None:
    ARQUIVO_DB.write_text(
        json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def registrar_disponibilidade(db: dict, online: bool) -> bool:
    """
    Atualiza estado de disponibilidade.
    Retorna True se o estado MUDOU (caiu ou voltou).
    """
    estava_online = db.get("site_online")
    db["site_online"] = online

    if online and estava_online is False:
        offline_desde = db.get("site_offline_desde")
        duracao = _calcular_duracao(offline_desde) if offline_desde else "?"
        db["site_offline_desde"] = None
        db["historico_disponibilidade"].append({
            "evento": "voltou",
            "em": datetime.now().isoformat(),
            "ficou_offline_por": duracao,
        })
        salvar_db(db)
        return True

    if not online and estava_online is not False:
        db["site_offline_desde"] = datetime.now().isoformat()
        db["historico_disponibilidade"].append({
            "evento": "caiu",
            "em": datetime.now().isoformat(),
        })
        salvar_db(db)
        return True

    return False


def _calcular_duracao(desde_iso: str) -> str:
    delta = datetime.now() - datetime.fromisoformat(desde_iso)
    total = int(delta.total_seconds())
    dias = total // 86400
    horas = (total % 86400) // 3600
    minutos = (total % 3600) // 60
    if dias > 0:
        return f"{dias}d {horas}h{minutos:02d}min"
    if horas > 0:
        return f"{horas}h{minutos:02d}min"
    return f"{minutos}min"


def tempo_offline(db: dict) -> str:
    offline_desde = db.get("site_offline_desde")
    return _calcular_duracao(offline_desde) if offline_desde else "desconhecido"


# ─────────────────────────────────────────────
# Verificação de disponibilidade (ping leve)
# ─────────────────────────────────────────────

def checar_site(url: str = URL_EDITAIS) -> tuple[bool, str]:
    """Ping rápido (timeout 10s). Retorna (online, mensagem)."""
    try:
        r = requests.get(
            url,
            verify=False,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (SENAI-Bot/2.0)"},
        )
        if r.status_code < 500:
            return True, f"Online ✅ (HTTP {r.status_code})"
        return False, f"Erro no servidor ❌ (HTTP {r.status_code})"
    except requests.exceptions.ConnectTimeout:
        return False, "Timeout de conexão ❌"
    except requests.exceptions.ConnectionError:
        return False, "Sem conexão com o servidor ❌"
    except Exception as exc:
        return False, f"Erro desconhecido ❌: {exc}"


# ─────────────────────────────────────────────
# Requisições com retry
# ─────────────────────────────────────────────

def fazer_request(url: str, stream: bool = False) -> requests.Response | None:
    """GET com retry exponencial e SSL desativado (site legado)."""
    for tentativa in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                verify=False,
                timeout=REQUEST_TIMEOUT,
                stream=stream,
                headers={"User-Agent": "Mozilla/5.0 (SENAI-Bot/2.0)"},
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            espera = 2 ** tentativa
            logger.warning(
                "Tentativa %d/%d falhou para %s: %s. Aguardando %ds...",
                tentativa, MAX_RETRIES, url, exc, espera,
            )
            if tentativa < MAX_RETRIES:
                time.sleep(espera)
    logger.error("Todas as tentativas falharam para: %s", url)
    return None


# ─────────────────────────────────────────────
# Scraping
# ─────────────────────────────────────────────

def pegar_editais() -> list[dict]:
    response = fazer_request(URL_EDITAIS)
    if not response:
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    editais: list[dict] = []
    for link in soup.find_all("a", href=True):
        titulo = link.get_text(strip=True)
        href: str = link["href"]
        if titulo.lower().startswith("edital") and ".pdf" in href.lower():
            if not href.startswith("http"):
                href = requests.compat.urljoin(URL_EDITAIS, href)
            editais.append({"titulo": titulo, "link": href})
    logger.info("%d edital(is) encontrado(s) no site.", len(editais))
    return editais


# ─────────────────────────────────────────────
# Filtros
# ─────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    return unidecode(texto.lower())


def edital_eh_cidade(titulo: str) -> bool:
    return CIDADE_ALVO in _normalizar(titulo)


def termos_ti_encontrados(texto: str) -> list[str]:
    t = _normalizar(texto)
    return [termo for termo in TI_TERMOS if termo in t]


# ─────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────

def extrair_texto_pdf(url_pdf: str) -> str | None:
    response = fazer_request(url_pdf, stream=True)
    if not response:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            for chunk in response.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp_path = tmp.name
        reader = PdfReader(tmp_path)
        texto = " ".join((p.extract_text() or "") for p in reader.pages)
        logger.info("  PDF: %d páginas, %d chars", len(reader.pages), len(texto))
        return texto
    except Exception as exc:
        logger.error("Erro ao processar PDF %s: %s", url_pdf, exc)
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ─────────────────────────────────────────────
# Busca completa de editais
# ─────────────────────────────────────────────

def buscar_novos_editais(progresso_cb=None) -> dict:
    """
    Faz scraping e filtragem completa. Persiste no DB.
    progresso_cb: callable(str) opcional para logs em tempo real.
    """
    def log(msg: str):
        logger.info(msg)
        if progresso_cb:
            progresso_cb(msg)

    db = carregar_db()
    db["total_buscas"] = db.get("total_buscas", 0) + 1
    db["ultima_busca_completa"] = datetime.now().isoformat()
    db["ultima_busca"] = datetime.now().isoformat()

    resultado = {
        "novos_aceitos": [],
        "novos_rejeitados": [],
        "erros": [],
        "total_site": 0,
        "ja_conhecidos": 0,
        "pdfs_baixados": 0,
    }

    links_conhecidos: set[str] = {
        e["link"] for e in db["aceitos"] + db["rejeitados"]
    }

    log("📄 Coletando editais da página...")
    editais_site = pegar_editais()
    resultado["total_site"] = len(editais_site)

    if not editais_site:
        log("⚠️ Nenhum edital encontrado na página.")
        salvar_db(db)
        return resultado

    log(f"📋 {len(editais_site)} edital(is) encontrado(s). Analisando...")

    for i, edital in enumerate(editais_site, 1):
        titulo = edital["titulo"]
        link   = edital["link"]

        if link in links_conhecidos:
            resultado["ja_conhecidos"] += 1
            continue

        log(f"\n🔎 [{i}/{len(editais_site)}] {titulo}")

        # 1) Filtro de cidade
        if not edital_eh_cidade(titulo):
            motivo = f"cidade (não contém '{CIDADE_ALVO}')"
            log(f"  ⏭ Ignorado — {motivo}")
            entry = {**edital, "motivo": motivo, "rejeitado_em": datetime.now().isoformat()}
            db["rejeitados"].append(entry)
            resultado["novos_rejeitados"].append(entry)
            continue

        # 2) Título menciona TI?
        termos = termos_ti_encontrados(titulo)
        if termos:
            log(f"  ✅ Aceito pelo título — termos: {', '.join(termos)}")
            edital.update({
                "aceito_em": datetime.now().isoformat(),
                "encontrado_em": "titulo",
                "termos_ti": termos,
            })
            db["aceitos"].append(edital)
            resultado["novos_aceitos"].append(edital)
            continue

        # 3) Verificar PDF
        log("  📥 Título genérico. Baixando PDF para análise...")
        resultado["pdfs_baixados"] += 1
        db["total_pdfs_baixados"] = db.get("total_pdfs_baixados", 0) + 1
        texto_pdf = extrair_texto_pdf(link)

        if texto_pdf is None:
            msg = f"Não foi possível analisar o PDF: {link}"
            log(f"  ⚠️ {msg}")
            resultado["erros"].append(msg)
            continue

        termos = termos_ti_encontrados(texto_pdf)
        if termos:
            log(f"  ✅ Aceito pelo PDF — termos: {', '.join(termos[:3])}")
            edital.update({
                "aceito_em": datetime.now().isoformat(),
                "encontrado_em": "pdf",
                "termos_ti": termos,
            })
            db["aceitos"].append(edital)
            resultado["novos_aceitos"].append(edital)
        else:
            motivo = "sem termos de TI (título e PDF verificados)"
            log(f"  ❌ Rejeitado — {motivo}")
            entry = {**edital, "motivo": motivo, "rejeitado_em": datetime.now().isoformat()}
            db["rejeitados"].append(entry)
            resultado["novos_rejeitados"].append(entry)

    salvar_db(db)
    log(
        f"\n📊 Concluído — ✅ {len(resultado['novos_aceitos'])} aceito(s), "
        f"❌ {len(resultado['novos_rejeitados'])} rejeitado(s), "
        f"📥 {resultado['pdfs_baixados']} PDF(s), "
        f"🔁 {resultado['ja_conhecidos']} já conhecidos."
    )
    return resultado


def reanalisar_rejeitados() -> dict:
    """Re-analisa rejeitados por conteúdo (não por cidade) com a lista atual de termos."""
    db = carregar_db()
    promovidos = []
    candidatos = [e for e in db["rejeitados"] if "cidade" not in e.get("motivo", "")]
    for edital in candidatos:
        termos = termos_ti_encontrados(edital.get("titulo", ""))
        if termos:
            edital.update({
                "aceito_em": datetime.now().isoformat(),
                "encontrado_em": "reanalise_titulo",
                "termos_ti": termos,
            })
            edital.pop("motivo", None)
            edital.pop("rejeitado_em", None)
            db["aceitos"].append(edital)
            db["rejeitados"].remove(edital)
            promovidos.append(edital)
    if promovidos:
        salvar_db(db)
    return {"promovidos": promovidos, "mantidos": len(candidatos) - len(promovidos)}


# ─────────────────────────────────────────────
# Telegram — Helpers
# ─────────────────────────────────────────────

def _escapar_md(texto: str) -> str:
    for c in r"_*[]()~`>#+-=|{}.!":
        texto = texto.replace(c, f"\\{c}")
    return texto


def formatar_edital(edital: dict) -> str:
    onde       = edital.get("encontrado_em", "?")
    termos     = edital.get("termos_ti", [])
    badge_map  = {"titulo": "📌 título", "pdf": "📄 PDF", "reanalise_titulo": "🔄 reanálise"}
    badge      = badge_map.get(onde, onde)
    termos_str = ", ".join(termos[:3]) if termos else "—"
    return (
        f"🎓 *Novo edital encontrado\\!*\n\n"
        f"📋 {_escapar_md(edital['titulo'])}\n"
        f"🔍 Encontrado em: {_escapar_md(badge)}\n"
        f"🏷 Termos TI: `{_escapar_md(termos_str)}`\n\n"
        f"[📥 Abrir PDF]({edital['link']})"
    )


async def _enviar_e_editar(update: Update, linhas: list[str], msg_id: int | None) -> int:
    texto = "\n".join(linhas[-20:])
    if len(texto) > 4096:
        texto = "…\n" + texto[-4090:]
    try:
        if msg_id:
            await update.message.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                text=texto,
            )
            return msg_id
    except Exception:
        pass
    msg = await update.message.reply_text(texto)
    return msg.message_id


async def _enviar_resultado_busca(send_fn, resultado: dict, prefixo: str = "🔍") -> None:
    """Envia resumo de busca. send_fn deve aceitar (text, **kwargs)."""
    novos = resultado["novos_aceitos"]
    if novos:
        await send_fn(
            f"{prefixo} *{len(novos)} novo\\(s\\) edital\\(is\\) de TI encontrado\\(s\\)\\!*",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        for edital in novos:
            await send_fn(
                formatar_edital(edital),
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )
    else:
        await send_fn(
            f"{prefixo} *Busca concluída\\.* Nenhum edital novo de TI\\.\n"
            f"📊 {resultado['total_site']} no site, "
            f"{resultado['ja_conhecidos']} já conhecidos\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ─────────────────────────────────────────────
# Telegram — Comandos
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Bot SENAI Editais — Cabo \\+ TI*\n\n"
        "Monitoro os editais do SENAI\\-PE e aviso quando surgir "
        "algo para *Cabo de Santo Agostinho* na área de *TI*\\.\n\n"
        "📋 *Comandos disponíveis:*\n"
        "/buscar — Busca editais agora\n"
        "/checar — Verifica se o site está online\n"
        "/listar — Exibe editais aceitos\n"
        "/rejeitados — Exibe editais rejeitados e motivos\n"
        "/status — Painel completo\n"
        "/forcar — Re\\-analisa editais rejeitados\n"
        "/auto — Ativa modo automático \\(monitor \\+ busca\\)\n"
        "/parar — Desativa o modo automático\n"
        "/ajuda — Exibe esta mensagem",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_checar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("🌐 Verificando\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)

    db = carregar_db()
    online_site,   status_site   = checar_site(URL_EDITAIS)
    online_portal, status_portal = checar_site(URL_PORTAL)
    mudou = registrar_disponibilidade(db, online_site)

    offline_info = ""
    if not online_site and db.get("site_offline_desde"):
        offline_info = f"\n⏱ Offline há: `{_escapar_md(tempo_offline(db))}`"

    texto = (
        f"🌐 *Status dos sistemas SENAI\\-PE*\n\n"
        f"📋 Site de editais: {_escapar_md(status_site)}\n"
        f"🎓 Portal do aluno: {_escapar_md(status_portal)}"
        f"{offline_info}\n\n"
        f"🕒 `{_escapar_md(datetime.now().strftime('%d/%m/%Y %H:%M:%S'))}`"
    )
    if mudou and online_site:
        texto += "\n\n🎉 *O site voltou\\!* Use /buscar\\."

    await msg.edit_text(texto, parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Busca manual com progresso em tempo real."""
    # Checar antes de tentar scraping
    online, motivo = checar_site()
    db = carregar_db()
    registrar_disponibilidade(db, online)

    if not online:
        await update.message.reply_text(
            f"❌ *Site indisponível*\n"
            f"Motivo: {_escapar_md(motivo)}\n"
            f"Offline há: `{_escapar_md(tempo_offline(db))}`\n\n"
            f"Use /auto para ser notificado quando o site voltar\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    log_linhas: list[str] = ["🔍 Iniciando busca..."]
    msg_id = None
    last_update = time.time()
    loop = asyncio.get_event_loop()

    async def progresso(linha: str):
        nonlocal msg_id, last_update
        log_linhas.append(linha)
        agora = time.time()
        if agora - last_update > 2 or any(k in linha for k in ("Concluído", "concluído", "⚠️")):
            msg_id = await _enviar_e_editar(update, log_linhas, msg_id)
            last_update = agora

    def progresso_sync(linha: str):
        asyncio.run_coroutine_threadsafe(progresso(linha), loop)

    resultado = await loop.run_in_executor(
        None, lambda: buscar_novos_editais(progresso_cb=progresso_sync)
    )

    await asyncio.sleep(0.5)
    await _enviar_e_editar(update, log_linhas, msg_id)
    await _enviar_resultado_busca(update.message.reply_text, resultado, "🔍")


async def cmd_listar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = carregar_db()
    aceitos = db.get("aceitos", [])
    if not aceitos:
        await update.message.reply_text(
            "📭 Nenhum edital aceito ainda\\. Use /buscar\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    await update.message.reply_text(
        f"📋 *{len(aceitos)} edital\\(is\\) aceito\\(s\\):*",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    for edital in aceitos[-20:]:
        await update.message.reply_text(
            formatar_edital(edital),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )


async def cmd_rejeitados(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = carregar_db()
    rejeitados = db.get("rejeitados", [])
    if not rejeitados:
        await update.message.reply_text("📭 Nenhum edital rejeitado registrado\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    por_motivo: dict[str, list] = {}
    for e in rejeitados:
        por_motivo.setdefault(e.get("motivo", "desconhecido"), []).append(e)

    linhas = [f"🗂 *{len(rejeitados)} edital\\(is\\) rejeitado\\(s\\):*\n"]
    for motivo, lista in por_motivo.items():
        linhas.append(f"*{_escapar_md(motivo)}* \\({len(lista)}\\)")
        for e in lista[-5:]:
            linhas.append(f"• {_escapar_md(e['titulo'])}")
        linhas.append("")

    await update.message.reply_text(
        "\n".join(linhas),
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = carregar_db()
    chat_id = update.effective_chat.id

    site_ok = db.get("site_online")
    if site_ok is True:
        site_str = "Online ✅"
    elif site_ok is False:
        site_str = f"Offline ❌ \\(há {_escapar_md(tempo_offline(db))}\\)"
    else:
        site_str = "Desconhecido ❓"

    ultima = db.get("ultima_busca_completa") or "nunca"
    if ultima != "nunca":
        ultima = ultima[:16].replace("T", " ")

    jobs_ativos = (
        context.job_queue.get_jobs_by_name(f"monitor_{chat_id}") or
        context.job_queue.get_jobs_by_name(f"busca_{chat_id}")
    )
    auto_str = "Ativo ✅" if jobs_ativos else "Inativo ⏸"

    historico = db.get("historico_disponibilidade", [])[-3:]
    hist_str = ""
    for ev in reversed(historico):
        em = ev["em"][:16].replace("T", " ")
        if ev["evento"] == "voltou":
            dur = ev.get("ficou_offline_por", "?")
            hist_str += f"\n  🟢 Voltou em {_escapar_md(em)} \\(offline por {_escapar_md(dur)}\\)"
        else:
            hist_str += f"\n  🔴 Caiu em {_escapar_md(em)}"

    texto = (
        f"📊 *Painel do Bot SENAI*\n\n"
        f"🌐 Site: {_escapar_md(site_str)}\n"
        f"⚙️ Modo automático: {_escapar_md(auto_str)}\n"
        f"✅ Aceitos: `{len(db.get('aceitos', []))}`\n"
        f"❌ Rejeitados: `{len(db.get('rejeitados', []))}`\n"
        f"🔍 Total de buscas: `{db.get('total_buscas', 0)}`\n"
        f"📥 PDFs analisados: `{db.get('total_pdfs_baixados', 0)}`\n"
        f"🕒 Última busca: `{_escapar_md(ultima)}`\n"
        f"🌐 Fonte: [pe\\.senai\\.br/editais]({URL_EDITAIS})"
    )
    if hist_str:
        texto += f"\n\n📅 *Histórico recente:*{hist_str}"

    await update.message.reply_text(texto, parse_mode=ParseMode.MARKDOWN_V2,
                                    disable_web_page_preview=True)


async def cmd_forcar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("🔄 Re\\-analisando rejeitados\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
    resultado = await asyncio.get_event_loop().run_in_executor(None, reanalisar_rejeitados)
    promovidos = resultado["promovidos"]
    mantidos   = resultado["mantidos"]
    if not promovidos:
        await msg.edit_text(
            f"🔄 Re\\-análise concluída\\. Nenhum promovido\\. {mantidos} mantido\\(s\\)\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    await msg.edit_text(
        f"🎉 *{len(promovidos)} edital\\(is\\) promovido\\(s\\)\\!* {mantidos} mantido\\(s\\)\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    for edital in promovidos:
        await update.message.reply_text(
            formatar_edital(edital),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ativa o modo automático unificado com dois jobs independentes:

    monitor_{chat_id}  — ping a cada 5min, notifica mudanças de estado,
                         dispara busca imediata quando o site volta.

    busca_{chat_id}    — busca completa a cada 24h, ignora silenciosamente
                         se o site estiver offline (o monitor já cuida disso).
    """
    chat_id = update.effective_chat.id

    ja_ativo = (
        context.job_queue.get_jobs_by_name(f"monitor_{chat_id}") or
        context.job_queue.get_jobs_by_name(f"busca_{chat_id}")
    )
    if ja_ativo:
        await update.message.reply_text(
            "⚙️ Modo automático já está ativo\\.\n"
            "Use /status para ver o estado ou /parar para desativar\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    context.job_queue.run_repeating(
        _job_monitor,
        interval=INTERVALO_MONITOR,
        first=15,
        chat_id=chat_id,
        name=f"monitor_{chat_id}",
    )

    context.job_queue.run_repeating(
        _job_busca,
        interval=INTERVALO_BUSCA,
        first=30,
        chat_id=chat_id,
        name=f"busca_{chat_id}",
    )

    await update.message.reply_text(
        "⚙️ *Modo automático ativado\\!*\n\n"
        f"👁 *Monitor* — verifica o site a cada {INTERVALO_MONITOR // 60}min\n"
        "  → Notifica quando o site *cair* ou *voltar*\n"
        "  → Quando voltar, já dispara uma busca imediatamente\n\n"
        f"🔍 *Busca diária* — varredura completa a cada 24h\n"
        "  → Só executa se o site estiver online\n"
        "  → Se offline, aguarda o monitor detectar a volta\n\n"
        "Use /parar para desativar\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_parar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parados = 0
    for nome in (f"monitor_{chat_id}", f"busca_{chat_id}"):
        for job in context.job_queue.get_jobs_by_name(nome):
            job.schedule_removal()
            parados += 1
    if not parados:
        await update.message.reply_text(
            "Não há tarefas automáticas ativas\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    await update.message.reply_text(
        "🛑 *Modo automático desativado\\.*\nUse /auto para reativar\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ─────────────────────────────────────────────
# Jobs automáticos
# ─────────────────────────────────────────────

async def _job_monitor(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ping leve a cada 5min.
    Notifica APENAS em mudanças de estado (caiu / voltou).
    Quando volta: dispara busca completa imediatamente.
    """
    chat_id = context.job.chat_id
    db = carregar_db()

    online, motivo = checar_site(URL_EDITAIS)
    mudou = registrar_disponibilidade(db, online)

    if not mudou:
        return  # silêncio — sem mudança

    if online:
        # Recuperar duração do offline do histórico
        offline_por = ""
        historico = db.get("historico_disponibilidade", [])
        if historico and historico[-1]["evento"] == "voltou":
            dur = historico[-1].get("ficou_offline_por", "?")
            offline_por = f"\nEstava offline há {_escapar_md(dur)}\\."

        logger.info("Site voltou. Disparando busca imediata para chat_id=%s", chat_id)

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🟢 *O site do SENAI\\-PE voltou\\!*"
                f"{offline_por}\n\n"
                f"🔍 Iniciando busca de editais automaticamente\\.\\.\\."
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        resultado = await asyncio.get_event_loop().run_in_executor(
            None, buscar_novos_editais
        )

        async def send(txt, **kw):
            await context.bot.send_message(chat_id=chat_id, text=txt, **kw)

        await _enviar_resultado_busca(send, resultado, "🟢")

    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🔴 *Site do SENAI\\-PE ficou offline\\!*\n\n"
                f"Motivo: {_escapar_md(motivo)}\n"
                f"Você será notificado quando voltar\\."
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )


async def _job_busca(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Busca completa a cada 24h.
    Se o site estiver offline: ignora silenciosamente.
    O _job_monitor já trata a notificação de queda/retorno e
    dispara a busca imediata quando o site voltar.
    """
    chat_id = context.job.chat_id
    logger.info("Job de busca diária para chat_id=%s", chat_id)

    online, _ = checar_site(URL_EDITAIS)
    db = carregar_db()
    registrar_disponibilidade(db, online)

    if not online:
        logger.info("Busca diária ignorada: site offline há %s", tempo_offline(db))
        return  # sem mensagem — o monitor já cuida disso

    try:
        resultado = await asyncio.get_event_loop().run_in_executor(
            None, buscar_novos_editais
        )

        async def send(txt, **kw):
            await context.bot.send_message(chat_id=chat_id, text=txt, **kw)

        await _enviar_resultado_busca(send, resultado, "⏰")

    except Exception as exc:
        logger.error("Erro no job de busca diária: %s", exc)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Erro na busca automática: {_escapar_md(str(exc))}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ─────────────────────────────────────────────
# Flask (health check para Render / Railway)
# ─────────────────────────────────────────────

flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return jsonify({"status": "ok", "bot": "SENAI Editais"})


@flask_app.route("/health")
def health():
    db = carregar_db()
    return jsonify({
        "status": "ok",
        "site_online": db.get("site_online"),
        "site_offline_desde": db.get("site_offline_desde"),
        "aceitos": len(db.get("aceitos", [])),
        "rejeitados": len(db.get("rejeitados", [])),
        "total_buscas": db.get("total_buscas", 0),
        "total_pdfs_baixados": db.get("total_pdfs_baixados", 0),
        "ultima_busca_completa": db.get("ultima_busca_completa"),
    })


def rodar_flask() -> None:
    port = int(os.environ.get("PORT", 10000))
    logger.info("Flask rodando na porta %d", port)
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)


# ─────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────

def main() -> None:
    if not TOKEN:
        raise ValueError("BOT_TOKEN não definido no .env")

    flask_thread = threading.Thread(target=rodar_flask, daemon=True)
    flask_thread.start()

    logger.info("Bot SENAI iniciando...")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("ajuda",      cmd_ajuda))
    app.add_handler(CommandHandler("buscar",     cmd_buscar))
    app.add_handler(CommandHandler("checar",     cmd_checar))
    app.add_handler(CommandHandler("listar",     cmd_listar))
    app.add_handler(CommandHandler("rejeitados", cmd_rejeitados))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("forcar",     cmd_forcar))
    app.add_handler(CommandHandler("auto",       cmd_auto))
    app.add_handler(CommandHandler("parar",      cmd_parar))

    async def set_commands(a: Application) -> None:
        await a.bot.set_my_commands([
            BotCommand("buscar",     "Buscar novos editais agora"),
            BotCommand("checar",     "Verificar se o site está online"),
            BotCommand("listar",     "Listar editais aceitos"),
            BotCommand("rejeitados", "Ver editais rejeitados e motivos"),
            BotCommand("status",     "Painel completo de informações"),
            BotCommand("forcar",     "Re-analisar editais rejeitados"),
            BotCommand("auto",       "Ativar modo automático (monitor + busca)"),
            BotCommand("parar",      "Desativar modo automático"),
            BotCommand("ajuda",      "Exibir ajuda"),
        ])

    app.post_init = set_commands
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
