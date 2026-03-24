"""
bot/commands/info.py
Comandos informativos: /start, /ajuda, /status
"""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.database import get_user
from bot.formatters import formatar_status
from config import Settings


def setup(settings: Settings):
    """Retorna os handlers deste módulo. Chamado pelo main.py."""

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Garante que o usuário existe no banco com os padrões do config.yaml
        get_user(update.effective_chat.id, settings.config_padrao())

        await update.message.reply_text(
            "👋 *Bot SENAI Editais*\n\n"
            "Monitoro os editais do SENAI\\-PE e aviso quando surgir algo "
            "na sua cidade e área de interesse\\.\n\n"
            "📋 *Busca e listagem:*\n"
            "/buscar — Busca editais agora\n"
            "/listar — Exibe editais aceitos\n"
            "/rejeitados — Exibe editais rejeitados e motivos\n"
            "/forcar — Re\\-analisa editais rejeitados\n\n"
            "📡 *Monitoramento:*\n"
            "/checar — Verifica se o site está online\n"
            "/auto — Ativa modo automático \\(monitor \\+ busca\\)\n"
            "/parar — Desativa o modo automático\n\n"
            "⚙️ *Configuração:*\n"
            "/config — Exibe ou altera sua configuração\n"
            "/addtermo — Adiciona um termo de busca\n"
            "/rmtermo — Remove um termo de busca\n"
            "/termos — Lista seus termos ativos\n"
            "/resetconfig — Volta para os padrões\n\n"
            "ℹ️ *Informações:*\n"
            "/status — Painel completo\n"
            "/ajuda — Exibe esta mensagem",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await cmd_start(update, context)

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        user = get_user(chat_id, settings.config_padrao())

        jobs_ativos = bool(
            context.job_queue.get_jobs_by_name(f"monitor_{chat_id}") or
            context.job_queue.get_jobs_by_name(f"busca_{chat_id}")
        )

        await update.message.reply_text(
            formatar_status(user, auto_ativo=jobs_ativos),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )

    return [cmd_start, cmd_ajuda, cmd_status]