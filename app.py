"""
Bot SENAI Editais - Versão Inteligente
Monitora editais do SENAI-PE filtrando por cidade e área de TI.
Inclui monitoramento de disponibilidade, logs verbosos e comandos extras.
"""

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta
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

URL_EDITAIS             = "https://www.pe.senai.br/editais/"
URL_PORTAL              = "https://sge.pe.senai.br"
ARQUIVO_DB              = Path("editais_cabo_ti.json")
TOKEN                   = os.getenv("BOT_TOKEN")
CIDADE_ALVO             = "cabo"
INTERVALO_AUTO          = 86_400      # 24h em segundos
INTERVALO_MONITOR       = 300         # 5 minutos — checa se site voltou
REQUEST_TIMEOUT         = 30
MAX_RETRIES             = 3
BACKOFF_OFFLINE_HORAS   = 6           # quando offline, tenta a cada 6h no lugar de 24h

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
    """Carrega o banco de dados local de editais já processados."""
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
    }


def salvar_db(db: dict) -> None:
    """Salva o banco de dados local."""
    db["ultima_busca"] = datetime.now().isoformat()
    ARQUIVO_DB.write_text(
        json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def registrar_disponibilidade(db: dict, online: bool) -> bool:
    """
    Atualiza o estado de disponibilidade do site no DB.
    Retorna True se o estado MUDOU (ex: voltou do offline).
    """
    estava_online = db.get("site_online")
    db["site_online"] = online

    if online and estava_online is False:
        # Site voltou!
        offline_desde = db.get("site_offline_desde")
        duracao = ""
        if offline_desde:
            delta = datetime.now() - datetime.fromisoformat(offline_desde)
            h, m = divmod(int(delta.total_seconds()), 3600)
            m = m // 60
            duracao = f"{h}h{m:02d}min"
        db["site_offline_desde"] = None
        db["historico_disponibilidade"].append({
            "evento": "voltou",
            "em": datetime.now().isoformat(),
            "ficou_offline_por": duracao,
        })
        salvar_db(db)
        return True  # MUDANÇA: offline → online

    if not online and estava_online is not False:
        # Site caiu agora
        db["site_offline_desde"] = datetime.now().isoformat()
        db["historico_disponibilidade"].append({
            "evento": "caiu",
            "em": datetime.now().isoformat(),
        })
        salvar_db(db)
        return True  # MUDANÇA: online → offline

    return False  # sem mudança


# ─────────────────────────────────────────────
# Verificação de disponibilidade
# ─────────────────────────────────────────────

def checar_site(url: str = URL_EDITAIS) -> tuple[bool, str]:
    """
    Verifica se o site está acessível.
    Retorna (online: bool, mensagem: str).
    """
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


def tempo_offline(db: dict) -> str:
    """Retorna string legível de quanto tempo o site está offline."""
    offline_desde = db.get("site_offline_desde")
    if not offline_desde:
        return "desconhecido"
    delta = datetime.now() - datetime.fromisoformat(offline_desde)
    total = int(delta.total_seconds())
    dias = total // 86400
    horas = (total % 86400) // 3600
    minutos = (total % 3600) // 60
    if dias > 0:
        return f"{dias}d {horas}h{minutos:02d}min"
    if horas > 0:
        return f"{horas}h{minutos:02d}min"
    return f"{minutos}min"


# ─────────────────────────────────────────────
# Requisições com retry
# ─────────────────────────────────────────────

def fazer_request(
    url: str,
    stream: bool = False,
    progresso_cb=None,
) -> requests.Response | None:
    """GET com retry exponencial e SSL desativado (site legado)."""
    for tentativa in range(1, MAX_RETRIES + 1):
        try:
            if progresso_cb and tentativa > 1:
                progresso_cb(f"⏳ Tentativa {tentativa}/{MAX_RETRIES}...")
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
    """Coleta todos os links de editais PDF na página do SENAI-PE."""
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


def contem_ti(texto: str) -> bool:
    t = _normalizar(texto)
    return any(termo in t for termo in TI_TERMOS)


def termos_ti_encontrados(texto: str) -> list[str]:
    """Retorna quais termos de TI foram encontrados no texto."""
    t = _normalizar(texto)
    return [termo for termo in TI_TERMOS if termo in t]


# ─────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────

def extrair_texto_pdf(url_pdf: str) -> str | None:
    """Baixa o PDF em arquivo temporário e extrai o texto."""
    response = fazer_request(url_pdf, stream=True)
    if not response:
        return None

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            for chunk in response.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp_path = tmp.name

        reader = PdfReader(tmp_path)
        texto = " ".join(
            (pagina.extract_text() or "") for pagina in reader.pages
        )
        logger.info("  PDF extraído: %d páginas, %d chars", len(reader.pages), len(texto))
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
# Lógica principal de busca
# ─────────────────────────────────────────────

def buscar_novos_editais(progresso_cb=None) -> dict:
    """
    Retorna dict com resultados detalhados da busca.
    progresso_cb: função opcional chamada com string de status.
    """
    def log(msg: str):
        logger.info(msg)
        if progresso_cb:
            progresso_cb(msg)

    db = carregar_db()
    db["total_buscas"] = db.get("total_buscas", 0) + 1

    # Checar disponibilidade primeiro
    log("🌐 Verificando disponibilidade do site...")
    online, motivo_check = checar_site()
    mudou = registrar_disponibilidade(db, online)

    resultado = {
        "site_online": online,
        "motivo_check": motivo_check,
        "mudou_estado": mudou,
        "novos_aceitos": [],
        "novos_rejeitados": [],
        "erros": [],
        "total_site": 0,
        "ja_conhecidos": 0,
        "pdfs_baixados": 0,
    }

    if not online:
        offline_ha = tempo_offline(db)
        log(f"❌ Site indisponível há {offline_ha}. Motivo: {motivo_check}")
        salvar_db(db)
        return resultado

    log(f"✅ Site online. Coletando editais...")

    links_conhecidos: set[str] = {
        e["link"] for e in db["aceitos"] + db["rejeitados"]
    }

    editais_site = pegar_editais()
    resultado["total_site"] = len(editais_site)

    if not editais_site:
        log("⚠️ Nenhum edital encontrado na página.")
        salvar_db(db)
        return resultado

    log(f"📄 {len(editais_site)} edital(is) na página. Analisando...")

    novos_aceitos: list[dict] = []
    novos_rejeitados: list[dict] = []

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
            novos_rejeitados.append(entry)
            continue

        # 2) Título menciona TI diretamente?
        termos = termos_ti_encontrados(titulo)
        if termos:
            log(f"  ✅ Aceito pelo título — termos: {', '.join(termos)}")
            edital["aceito_em"] = datetime.now().isoformat()
            edital["encontrado_em"] = "titulo"
            edital["termos_ti"] = termos
            db["aceitos"].append(edital)
            novos_aceitos.append(edital)
            continue

        # 3) Verificar conteúdo do PDF
        log(f"  📥 Título genérico. Baixando PDF para análise...")
        resultado["pdfs_baixados"] += 1
        db["total_pdfs_baixados"] = db.get("total_pdfs_baixados", 0) + 1
        texto_pdf = extrair_texto_pdf(link)

        if texto_pdf is None:
            msg = f"Não foi possível baixar/analisar o PDF: {link}"
            log(f"  ⚠️ {msg}")
            resultado["erros"].append(msg)
            continue

        termos = termos_ti_encontrados(texto_pdf)
        if termos:
            log(f"  ✅ Aceito pelo PDF — termos: {', '.join(termos[:3])}")
            edital["aceito_em"] = datetime.now().isoformat()
            edital["encontrado_em"] = "pdf"
            edital["termos_ti"] = termos
            db["aceitos"].append(edital)
            novos_aceitos.append(edital)
        else:
            motivo = "sem termos de TI (título e PDF verificados)"
            log(f"  ❌ Rejeitado — {motivo}")
            entry = {**edital, "motivo": motivo, "rejeitado_em": datetime.now().isoformat()}
            db["rejeitados"].append(entry)
            novos_rejeitados.append(entry)

    resultado["novos_aceitos"]    = novos_aceitos
    resultado["novos_rejeitados"] = novos_rejeitados

    salvar_db(db)

    log(
        f"\n📊 Busca concluída — "
        f"✅ {len(novos_aceitos)} aceito(s), "
        f"❌ {len(novos_rejeitados)} rejeitado(s), "
        f"📥 {resultado['pdfs_baixados']} PDF(s) baixado(s), "
        f"🔁 {resultado['ja_conhecidos']} já conhecidos."
    )
    return resultado


def reanalisar_rejeitados() -> dict:
    """
    Re-analisa editais rejeitados por 'sem_ti' ou motivo genérico.
    Útil quando novos termos são adicionados à lista TI_TERMOS.
    """
    db = carregar_db()
    reanalise = {"promovidos": [], "mantidos": 0}

    candidatos = [
        e for e in db["rejeitados"]
        if "cidade" not in e.get("motivo", "")
    ]

    for edital in candidatos:
        termos = termos_ti_encontrados(edital.get("titulo", ""))
        if termos:
            edital["aceito_em"] = datetime.now().isoformat()
            edital["encontrado_em"] = "reanalise_titulo"
            edital["termos_ti"] = termos
            edital.pop("motivo", None)
            edital.pop("rejeitado_em", None)
            db["aceitos"].append(edital)
            db["rejeitados"].remove(edital)
            reanalise["promovidos"].append(edital)
        else:
            reanalise["mantidos"] += 1

    if reanalise["promovidos"]:
        salvar_db(db)

    return reanalise


# ─────────────────────────────────────────────
# Telegram — Helpers
# ─────────────────────────────────────────────

def _escapar_md(texto: str) -> str:
    """Escapa caracteres especiais do MarkdownV2 do Telegram."""
    caracteres = r"_*[]()~`>#+-=|{}.!"
    for c in caracteres:
        texto = texto.replace(c, f"\\{c}")
    return texto


def formatar_edital(edital: dict) -> str:
    onde = edital.get("encontrado_em", "?")
    termos = edital.get("termos_ti", [])
    termos_str = ", ".join(termos[:3]) if termos else "—"
    badge = "📌 título" if onde == "titulo" else "📄 PDF"

    return (
        f"🎓 *Novo edital encontrado\\!*\n\n"
        f"📋 {_escapar_md(edital['titulo'])}\n"
        f"🔍 Encontrado em: {_escapar_md(badge)}\n"
        f"🏷 Termos TI: `{_escapar_md(termos_str)}`\n\n"
        f"[📥 Abrir PDF]({edital['link']})"
    )


async def _enviar_progresso(
    update: Update,
    linhas: list[str],
    msg_id: int | None = None,
) -> int:
    """Edita ou envia nova mensagem de progresso. Retorna message_id."""
    texto = "\n".join(linhas[-20:])  # últimas 20 linhas
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
        else:
            msg = await update.message.reply_text(texto)
            return msg.message_id
    except Exception:
        msg = await update.message.reply_text(texto)
        return msg.message_id


# ─────────────────────────────────────────────
# Telegram — Comandos
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Bot SENAI Editais — Cabo \\+ TI*\n\n"
        "Monitoro os editais do SENAI\\-PE e aviso quando surgir "
        "algo para *Cabo de Santo Agostinho* na área de *TI*\\.\n\n"
        "📋 *Comandos disponíveis:*\n"
        "/buscar — Busca novos editais agora\n"
        "/checar — Verifica se o site está online\n"
        "/listar — Exibe todos os editais aceitos\n"
        "/rejeitados — Exibe editais rejeitados\n"
        "/status — Painel completo de informações\n"
        "/forcar — Re\\-analisa editais rejeitados\n"
        "/auto — Ativa busca automática \\(24h\\)\n"
        "/monitor — Ativa monitor de disponibilidade \\(5min\\)\n"
        "/parar — Desativa todas as tarefas automáticas\n"
        "/ajuda — Exibe esta mensagem",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_checar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verifica se o site está online e mostra detalhes."""
    msg = await update.message.reply_text("🌐 Verificando site do SENAI\\-PE\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)

    db = carregar_db()

    # Checar site principal + portal do aluno
    online_site, status_site   = checar_site(URL_EDITAIS)
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
        f"🕒 Verificado em: `{_escapar_md(datetime.now().strftime('%d/%m/%Y %H:%M:%S'))}`"
    )

    if mudou and online_site:
        texto += "\n\n🎉 *O site voltou\\!* Agora você pode usar /buscar\\."

    await msg.edit_text(texto, parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Busca novos editais com logs verbosos em tempo real."""
    log_linhas: list[str] = ["🔍 Iniciando busca de editais..."]
    msg_id = None
    last_update = time.time()

    async def progresso(linha: str):
        nonlocal msg_id, last_update
        log_linhas.append(linha)
        agora = time.time()
        # Throttle: atualiza no máximo a cada 2s para não spammar a API
        if agora - last_update > 2 or "concluída" in linha or "indisponível" in linha:
            msg_id = await _enviar_progresso(update, log_linhas, msg_id)
            last_update = agora

    def progresso_sync(linha: str):
        asyncio.get_event_loop().call_soon_threadsafe(
            lambda: asyncio.ensure_future(progresso(linha))
        )

    resultado = await asyncio.get_event_loop().run_in_executor(
        None, lambda: buscar_novos_editais(progresso_cb=progresso_sync)
    )

    # Aguarda último update de progresso
    await asyncio.sleep(0.5)
    msg_id = await _enviar_progresso(update, log_linhas, msg_id)

    # Resultado final
    novos = resultado["novos_aceitos"]

    if not resultado["site_online"]:
        offline_ha = tempo_offline(carregar_db())
        await update.message.reply_text(
            f"⚠️ *Site indisponível\\.*\n"
            f"Offline há `{_escapar_md(offline_ha)}`\\.\n"
            f"Use /monitor para ser avisado quando voltar\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if not novos:
        await update.message.reply_text(
            f"✅ Busca concluída\\. Nenhum edital *novo* de TI encontrado\\.\n"
            f"📊 {resultado['total_site']} edital\\(is\\) no site, "
            f"{resultado['ja_conhecidos']} já conhecidos\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    await update.message.reply_text(
        f"🎉 *{len(novos)} novo\\(s\\) edital\\(is\\) de TI encontrado\\(s\\)\\!*",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    for edital in novos:
        await update.message.reply_text(
            formatar_edital(edital),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )


async def cmd_listar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = carregar_db()
    aceitos = db.get("aceitos", [])

    if not aceitos:
        await update.message.reply_text(
            "📭 Nenhum edital aceito ainda\\. Use /buscar para checar\\.",
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
    """Exibe os editais rejeitados com o motivo."""
    db = carregar_db()
    rejeitados = db.get("rejeitados", [])

    if not rejeitados:
        await update.message.reply_text("📭 Nenhum edital rejeitado registrado.")
        return

    # Agrupar por motivo
    por_motivo: dict[str, list] = {}
    for e in rejeitados:
        m = e.get("motivo", "desconhecido")
        por_motivo.setdefault(m, []).append(e)

    linhas = [f"🗂 *{len(rejeitados)} edital\\(is\\) rejeitado\\(s\\):*\n"]
    for motivo, lista in por_motivo.items():
        linhas.append(f"*Motivo: {_escapar_md(motivo)}* \\({len(lista)}\\)")
        for e in lista[-5:]:  # últimos 5 por motivo
            linhas.append(f"• {_escapar_md(e['titulo'])}")
        linhas.append("")

    await update.message.reply_text(
        "\n".join(linhas),
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = carregar_db()
    ultima = db.get("ultima_busca") or "nunca"
    site_ok = db.get("site_online")

    if site_ok is True:
        site_str = "Online ✅"
    elif site_ok is False:
        site_str = f"Offline ❌ \\(há {_escapar_md(tempo_offline(db))}\\)"
    else:
        site_str = "Desconhecido ❓"

    # Últimos eventos de disponibilidade
    historico = db.get("historico_disponibilidade", [])[-3:]
    hist_str = ""
    for ev in reversed(historico):
        em = ev["em"][:16].replace("T", " ")
        if ev["evento"] == "voltou":
            dur = ev.get("ficou_offline_por", "?")
            hist_str += f"\n  🟢 Voltou em {_escapar_md(em)} \\(ficou {_escapar_md(dur)} offline\\)"
        else:
            hist_str += f"\n  🔴 Caiu em {_escapar_md(em)}"

    texto = (
        f"📊 *Painel do Bot SENAI*\n\n"
        f"🌐 Site: {_escapar_md(site_str)}\n"
        f"✅ Aceitos: `{len(db.get('aceitos', []))}`\n"
        f"❌ Rejeitados: `{len(db.get('rejeitados', []))}`\n"
        f"🔍 Total de buscas: `{db.get('total_buscas', 0)}`\n"
        f"📥 PDFs analisados: `{db.get('total_pdfs_baixados', 0)}`\n"
        f"🕒 Última busca: `{_escapar_md(ultima[:16].replace('T', ' ') if ultima != 'nunca' else 'nunca')}`\n"
        f"🌐 Fonte: [pe\\.senai\\.br/editais]({URL_EDITAIS})"
    )

    if hist_str:
        texto += f"\n\n📅 *Histórico recente:*{hist_str}"

    await update.message.reply_text(
        texto,
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
    )


async def cmd_forcar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-analisa editais rejeitados por conteúdo (não por cidade)."""
    msg = await update.message.reply_text("🔄 Re\\-analisando editais rejeitados\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)

    resultado = await asyncio.get_event_loop().run_in_executor(
        None, reanalisar_rejeitados
    )

    promovidos = resultado["promovidos"]
    mantidos   = resultado["mantidos"]

    if not promovidos:
        await msg.edit_text(
            f"🔄 Re\\-análise concluída\\.\n"
            f"Nenhum edital foi reclassificado\\. "
            f"{mantidos} mantido\\(s\\) como rejeitado\\(s\\)\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    await msg.edit_text(
        f"🎉 *{len(promovidos)} edital\\(is\\) promovido\\(s\\) para aceito\\!*\n"
        f"{mantidos} mantido\\(s\\) como rejeitado\\(s\\)\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    for edital in promovidos:
        await update.message.reply_text(
            formatar_edital(edital),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(f"auto_{chat_id}")

    if jobs:
        await update.message.reply_text("⏰ Busca automática já está ativa\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    context.job_queue.run_repeating(
        _job_diario,
        interval=INTERVALO_AUTO,
        first=10,
        chat_id=chat_id,
        name=f"auto_{chat_id}",
        data=chat_id,
    )
    await update.message.reply_text(
        "⏰ Busca automática *ativada\\!*\n"
        "Verificarei novos editais a cada 24h\\.\n"
        "Use /monitor para também monitorar quando o site voltar\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ativa monitoramento de disponibilidade do site a cada 5 minutos."""
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(f"monitor_{chat_id}")

    if jobs:
        await update.message.reply_text("👁 Monitor já está ativo\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    context.job_queue.run_repeating(
        _job_monitor,
        interval=INTERVALO_MONITOR,
        first=5,
        chat_id=chat_id,
        name=f"monitor_{chat_id}",
        data=chat_id,
    )
    await update.message.reply_text(
        "👁 *Monitor ativado\\!*\n"
        f"Verificarei o site a cada {INTERVALO_MONITOR // 60} minutos\\.\n"
        "Você será notificado quando o site cair *ou voltar*\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_parar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    parados = []

    for prefixo in ("auto_", "monitor_"):
        jobs = context.job_queue.get_jobs_by_name(f"{prefixo}{chat_id}")
        for job in jobs:
            job.schedule_removal()
            parados.append(prefixo.rstrip("_"))

    if not parados:
        await update.message.reply_text("Não há tarefas automáticas ativas\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    nomes = " e ".join(parados)
    await update.message.reply_text(
        f"🛑 Tarefas desativadas: *{_escapar_md(nomes)}*\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ─────────────────────────────────────────────
# Jobs automáticos
# ─────────────────────────────────────────────

async def _job_diario(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    logger.info("Job diário iniciado para chat_id=%s", chat_id)

    try:
        resultado = await asyncio.get_event_loop().run_in_executor(
            None, buscar_novos_editais
        )

        if not resultado["site_online"]:
            db = carregar_db()
            offline_ha = tempo_offline(db)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⏰ *Busca automática realizada\\.*\n\n"
                    f"❌ Site indisponível há `{_escapar_md(offline_ha)}`\\.\n"
                    f"Ative /monitor para ser avisado quando voltar\\."
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        novos = resultado["novos_aceitos"]
        if novos:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ *Busca automática:* {len(novos)} novo\\(s\\) edital\\(is\\) encontrado\\(s\\)\\!",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            for edital in novos:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=formatar_edital(edital),
                    parse_mode=ParseMode.MARKDOWN_V2,
                    disable_web_page_preview=True,
                )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⏰ *Busca automática realizada\\.*\n"
                    f"Nenhum edital novo encontrado\\.\n"
                    f"📊 {resultado['total_site']} no site, "
                    f"{resultado['ja_conhecidos']} já conhecidos\\."
                ),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
    except Exception as exc:
        logger.error("Erro no job diário: %s", exc)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Erro na busca automática: {_escapar_md(str(exc))}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


async def _job_monitor(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verifica disponibilidade e notifica apenas em mudanças de estado."""
    chat_id = context.job.chat_id
    db = carregar_db()

    online, motivo = checar_site(URL_EDITAIS)
    mudou = registrar_disponibilidade(db, online)

    if not mudou:
        return  # Sem mudança, silêncio total

    if online:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🟢 *O site do SENAI\\-PE voltou\\!*\n\n"
                "O site estava offline e agora está acessível\\.\n"
                "Use /buscar para verificar novos editais\\."
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
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
        "ultima_busca": db.get("ultima_busca"),
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
    app.add_handler(CommandHandler("monitor",    cmd_monitor))
    app.add_handler(CommandHandler("parar",      cmd_parar))

    async def set_commands(app: Application) -> None:
        await app.bot.set_my_commands([
            BotCommand("buscar",     "Buscar novos editais agora"),
            BotCommand("checar",     "Verificar se o site está online"),
            BotCommand("listar",     "Listar editais aceitos"),
            BotCommand("rejeitados", "Ver editais rejeitados e motivos"),
            BotCommand("status",     "Painel completo de informações"),
            BotCommand("forcar",     "Re-analisar editais rejeitados"),
            BotCommand("auto",       "Ativar busca automática (24h)"),
            BotCommand("monitor",    "Monitorar disponibilidade do site"),
            BotCommand("parar",      "Desativar tarefas automáticas"),
            BotCommand("ajuda",      "Exibir ajuda"),
        ])

    app.post_init = set_commands

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
