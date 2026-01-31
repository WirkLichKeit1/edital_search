import requests
from bs4 import BeautifulSoup
import json
import os
from pypdf import PdfReader
from unidecode import unidecode
import urllib3
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

# =============================
# CONFIGURAÇÕES
# =============================

URL = "https://www.pe.senai.br/editais/"
ARQUIVO = "editais_cabo_ti.json"

# Coloque seu token aqui
TOKEN = os.getenv("BOT_TOKEN")

# Cidade alvo
CIDADE = "cabo"

# Cursos/termos TI (no título ou no PDF)
TI_TERMOS = [
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
    "tecnologia da informacao"
]

# =============================
# 1. Buscar editais no site
# =============================
def pegar_editais():
    r = requests.get(URL, verify=certifi.where())
    soup = BeautifulSoup(r.text, "html.parser")

    editais = []

    for link in soup.find_all("a"):
        titulo = link.get_text(strip=True)
        href = link.get("href")

        if titulo.lower().startswith("edital") and href:
            if ".pdf" in href.lower():
                editais.append({
                    "titulo": titulo,
                    "link": href
                })

    return editais


# =============================
# 2. Verificar se é do Cabo
# =============================
def edital_eh_cabo(titulo):
    t = unidecode(titulo.lower())
    return CIDADE in t


# =============================
# 3. Verificar se título já indica TI
# =============================
def titulo_indica_ti(titulo):
    t = unidecode(titulo.lower())
    return any(p in t for p in TI_TERMOS)


# =============================
# 4. Baixar PDF temporário
# =============================
def baixar_pdf(url_pdf):
    nome = "temp.pdf"
    r = requests.get(url_pdf, verify=False)

    with open(nome, "wb") as f:
        f.write(r.content)

    return nome


# =============================
# 5. Extrair texto do PDF
# =============================
def extrair_texto_pdf(arquivo):
    reader = PdfReader(arquivo)
    texto = ""

    for pagina in reader.pages:
        texto += pagina.extract_text() or ""

    return unidecode(texto.lower())


# =============================
# 6. Verificar se PDF contém TI
# =============================
def pdf_contem_ti(texto_pdf):
    return any(p in texto_pdf for p in TI_TERMOS)


# =============================
# FUNÇÃO PRINCIPAL DE BUSCA
# =============================
def buscar_novos_editais():
    editais_site = pegar_editais()

    # carregar aceitos já salvos
    if os.path.exists(ARQUIVO):
        aceitos = json.load(open(ARQUIVO))
    else:
        aceitos = []

    aceitos_links = {e["link"] for e in aceitos}

    novos_aceitos = []

    for edital in editais_site:

        # já aceito antes
        if edital["link"] in aceitos_links:
            continue

        titulo = edital["titulo"]
        link = edital["link"]

        print("\n📄 Avaliando:", titulo)

        # 1) Só Cabo
        if not edital_eh_cabo(titulo):
            print("⏭ Ignorado (não é Cabo)")
            continue

        # 2) Se título já diz TI → aceita direto
        if titulo_indica_ti(titulo):
            print("🚨 ACEITO direto (título menciona TI)")
            aceitos.append(edital)
            novos_aceitos.append(edital)
            continue

        # 3) Título genérico → baixar PDF e verificar
        print("⬇️ Título genérico → baixando PDF para checar cursos...")

        try:
            arquivo = baixar_pdf(link)
            texto_pdf = extrair_texto_pdf(arquivo)

            if pdf_contem_ti(texto_pdf):
                print("🚨 ACEITO (PDF contém curso TI!)")
                aceitos.append(edital)
                novos_aceitos.append(edital)
            else:
                print("❌ Rejeitado (PDF não tem TI)")

            # apagar PDF temporário
            os.remove(arquivo)

        except Exception as e:
            print("⚠️ Erro ao analisar PDF:", e)

    # salvar apenas aceitos
    json.dump(aceitos, open(ARQUIVO, "w"), indent=2)

    return novos_aceitos


# =============================
# COMANDOS DO TELEGRAM
# =============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot SENAI Editais ativo!\n\n"
        "Use:\n"
        "/buscar → buscar editais agora\n"
        "/auto → ativar busca automática (1x por dia)"
    )


async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Buscando editais novos CABO + TI...")

    novos = buscar_novos_editais()

    if not novos:
        await update.message.reply_text("✅ Nenhum edital novo encontrado.")
    else:
        for e in novos:
            msg = (
                f"🚨 Novo edital encontrado!\n\n"
                f"📄 {e['titulo']}\n"
                f"🔗 {e['link']}"
            )
            await update.message.reply_text(msg)


# =============================
# JOB AUTOMÁTICO (24h)
# =============================

async def job_diario(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    novos = buscar_novos_editais()

    if novos:
        for e in novos:
            msg = (
                f"🚨 Novo edital CABO + TI!\n\n"
                f"📄 {e['titulo']}\n"
                f"🔗 {e['link']}"
            )
            await context.bot.send_message(chat_id=chat_id, text=msg)


async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id

    context.job_queue.run_repeating(
        job_diario,
        interval=86400,  # 24h
        first=10,
        chat_id=chat_id
    )

    await update.message.reply_text(
        "✅ Busca automática ativada!\n"
        "O bot vai checar novos editais a cada 24h."
    )


# =============================
# MAIN
# =============================

def main():
    print("🤖 Bot rodando...")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buscar", buscar))
    app.add_handler(CommandHandler("auto", auto))

    app.run_polling()


if __name__ == "__main__":
    main()
