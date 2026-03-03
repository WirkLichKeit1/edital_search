"""
Bot SENAI Editais - Versão Profissional
Monitora editais do SENAI-PE filtrando por cidade e área de TI.
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

URL_EDITAIS     = "https://www.pe.senai.br/editais/"
ARQUIVO_DB      = Path("editais_cabo_ti.json")
TOKEN           = os.getenv("BOT_TOKEN")
CIDADE_ALVO     = "cabo"
INTERVALO_AUTO  = 86_400          # 24 horas em segundos
REQUEST_TIMEOUT = 30              # segundos
MAX_RETRIES     = 3

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
    return {"aceitos": [], "rejeitados": [], "ultima_busca": None}


def salvar_db(db: dict) -> None:
    """Salva o banco de dados local."""
    db["ultima_busca"] = datetime.now().isoformat()
    ARQUIVO_DB.write_text(
        json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8"
    )


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
            # Garantir URL absoluta
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

def buscar_novos_editais() -> list[dict]:
    """
    Retorna lista de editais novos aceitos (Cabo + TI).
    Persiste resultados no DB local.
    """
    db = carregar_db()
    links_conhecidos: set[str] = {
        e["link"] for e in db["aceitos"] + db["rejeitados"]
    }

    editais_site = pegar_editais()
    novos_aceitos: list[dict] = []

    for edital in editais_site:
        titulo = edital["titulo"]
        link   = edital["link"]

        if link in links_conhecidos:
            continue

        logger.info("Avaliando: %s", titulo)

        # 1) Filtro de cidade
        if not edital_eh_cidade(titulo):
            logger.info("  ⏭  Ignorado (cidade não é '%s')", CIDADE_ALVO)
            db["rejeitados"].append({**edital, "motivo": "cidade"})
            continue

        # 2) Título menciona TI diretamente?
        if contem_ti(titulo):
            logger.info("Aceito (título menciona TI)")
            edital["aceito_em"] = datetime.now().isoformat()
            db["aceitos"].append(edital)
            novos_aceitos.append(edital)
            continue

        # 3) Verificar conteúdo do PDF
        logger.info("Baixando PDF para verificar cursos...")
        texto_pdf = extrair_texto_pdf(link)

        if texto_pdf is None:
            logger.warning("Não foi possível analisar o PDF: %s", link)
            continue

        if contem_ti(texto_pdf):
            logger.info("Aceito (PDF menciona TI)")
            edital["aceito_em"] = datetime.now().isoformat()
            db["aceitos"].append(edital)
            novos_aceitos.append(edital)
        else:
            logger.info("Rejeitado (PDF não menciona TI)")
            db["rejeitados"].append({**edital, "motivo": "sem_ti"})

    salvar_db(db)
    logger.info(
        "Busca concluída. %d novo(s) edital(is) aceito(s).", len(novos_aceitos)
    )
    return novos_aceitos


# ─────────────────────────────────────────────
# Telegram — Helpers de mensagem
# ─────────────────────────────────────────────

def formatar_edital(edital: dict) -> str:
    return (
        f"*Novo edital encontrado\\!*\n\n"
        f"{_escapar_md(edital['titulo'])}\n"
        f"[Abrir PDF]({edital['link']})"
    )


def _escapar_md(texto: str) -> str:
    """Escapa caracteres especiais do MarkdownV2 do Telegram."""
    caracteres = r"_*[]()~`>#+-=|{}.!"
    for c in caracteres:
        texto = texto.replace(c, f"\\{c}")
    return texto


# ─────────────────────────────────────────────
# Telegram — Comandos
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Bot SENAI Editais — Cabo \\+ TI*\n\n"
        "Monitoro os editais do SENAI\\-PE e aviso quando surgir "
        "algo para *Cabo de Santo Agostinho* na área de *TI*\\.\n\n"
        "📋 *Comandos disponíveis:*\n"
        "/buscar — Verifica editais agora\n"
        "/listar — Exibe todos os editais aceitos\n"
        "/status — Informações do banco de dados\n"
        "/auto — Ativa a busca automática \\(24h\\)\n"
        "/parar — Desativa a busca automática\n"
        "/ajuda — Exibe esta mensagem",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("🔍 Buscando editais… Aguarde.")

    novos = await asyncio.get_event_loop().run_in_executor(
        None, buscar_novos_editais
    )

    if not novos:
        await msg.edit_text("Nenhum edital novo encontrado.")
        return

    await msg.edit_text(f"{len(novos)} edital\\(is\\) encontrado\\(s\\)\\!", parse_mode=ParseMode.MARKDOWN_V2)
    for edital in novos:
        await update.message.reply_text(
            formatar_edital(edital), parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )


async def cmd_listar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = carregar_db()
    aceitos = db.get("aceitos", [])

    if not aceitos:
        await update.message.reply_text("📭 Nenhum edital aceito ainda. Use /buscar para checar.")
        return

    await update.message.reply_text(
        f"📋 *{len(aceitos)} edital\\(is\\) aceito\\(s\\):*",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    for edital in aceitos[-20:]:   # limite de 20 para não spam
        await update.message.reply_text(
            formatar_edital(edital),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = carregar_db()
    ultima = db.get("ultima_busca") or "nunca"

    texto = (
        f"📊 *Status do Bot*\n\n"
        f"✅ Aceitos: `{len(db.get('aceitos', []))}`\n"
        f"❌ Rejeitados: `{len(db.get('rejeitados', []))}`\n"
        f"🕒 Última busca: `{_escapar_md(ultima)}`\n"
        f"🌐 Fonte: [pe\\.senai\\.br/editais]({URL_EDITAIS})"
    )
    await update.message.reply_text(texto, parse_mode=ParseMode.MARKDOWN_V2,
                                    disable_web_page_preview=True)


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(f"auto_{chat_id}")

    if jobs:
        await update.message.reply_text("Busca automática já está ativa.")
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
        "Busca automática *ativada\\!*\n"
        "Verificarei novos editais a cada 24h\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_parar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(f"auto_{chat_id}")

    if not jobs:
        await update.message.reply_text("Não há busca automática ativa.")
        return

    for job in jobs:
        job.schedule_removal()

    await update.message.reply_text("Busca automática *desativada\\.*", parse_mode=ParseMode.MARKDOWN_V2)


# ─────────────────────────────────────────────
# Job automático
# ─────────────────────────────────────────────

async def _job_diario(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id
    logger.info("Job diário iniciado para chat_id=%s", chat_id)

    try:
        novos = await asyncio.get_event_loop().run_in_executor(
            None, buscar_novos_editais
        )
        if novos:
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
                text="*Busca diária realizada\\.* Nenhum edital novo encontrado\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
    except Exception as exc:
        logger.error("Erro no job diário: %s", exc)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Erro na busca automática: {exc}",
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
        "aceitos": len(db.get("aceitos", [])),
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

    # Flask em thread separada
    flask_thread = threading.Thread(target=rodar_flask, daemon=True)
    flask_thread.start()

    logger.info("Bot SENAI iniciando...")

    app = Application.builder().token(TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("ajuda",  cmd_ajuda))
    app.add_handler(CommandHandler("buscar", cmd_buscar))
    app.add_handler(CommandHandler("listar", cmd_listar))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("auto",   cmd_auto))
    app.add_handler(CommandHandler("parar",  cmd_parar))

    # Menu de comandos visível no Telegram
    async def set_commands(app: Application) -> None:
        await app.bot.set_my_commands([
            BotCommand("buscar", "Verificar editais agora"),
            BotCommand("listar", "Listar editais aceitos"),
            BotCommand("status", "Status do banco de dados"),
            BotCommand("auto",   "Ativar busca automática (24h)"),
            BotCommand("parar",  "Desativar busca automática"),
            BotCommand("ajuda",  "Exibir ajuda"),
        ])

    app.post_init = set_commands

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
