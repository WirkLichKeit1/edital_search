"""
bot/commands/monitor.py
Comandos de monitoramento: /checar, /auto, /parar
"""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.database import get_user, registrar_disponibilidade
from bot.formatters import esc
from bot.scraper import checar_site, tempo_offline
from config import Settings


def setup(settings: Settings):
    """Retorna os handlers deste módulo. Chamado pelo main.py."""

    async def cmd_checar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        msg = await update.message.reply_text("🌐 Verificando\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)

        user = get_user(chat_id, settings.config_padrao())
        online_site,   status_site   = await checar_site(settings.url_editais)
        online_portal, status_portal = await checar_site(settings.url_portal)
        mudou = registrar_disponibilidade(chat_id, online_site)

        offline_info = ""
        if not online_site and user.site_offline_desde:
            offline_info = f"\n⏱ Offline há: `{esc(tempo_offline(user))}`"

        texto = (
            f"🌐 *Status dos sistemas SENAI\\-PE*\n\n"
            f"📋 Site de editais: {esc(status_site)}\n"
            f"🎓 Portal do aluno: {esc(status_portal)}"
            f"{offline_info}\n\n"
            f"🕒 Verificado agora\\."
        )

        if mudou and online_site:
            texto += "\n\n🎉 *O site voltou\\!* Use /buscar\\."

        await msg.edit_text(texto, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from bot.jobs import job_monitor, job_busca

        chat_id = update.effective_chat.id
        user    = get_user(chat_id, settings.config_padrao())

        ja_ativo = bool(
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

        intervalo_monitor = user.config.intervalo_monitor
        intervalo_busca   = user.config.intervalo_busca

        context.job_queue.run_repeating(
            job_monitor,
            interval=intervalo_monitor,
            first=15,
            chat_id=chat_id,
            name=f"monitor_{chat_id}",
            data={"settings": settings},
        )

        context.job_queue.run_repeating(
            job_busca,
            interval=intervalo_busca,
            first=30,
            chat_id=chat_id,
            name=f"busca_{chat_id}",
            data={"settings": settings},
        )

        monitor_min = intervalo_monitor // 60
        busca_h     = intervalo_busca // 3600

        await update.message.reply_text(
            "⚙️ *Modo automático ativado\\!*\n\n"
            f"👁 *Monitor* — verifica o site a cada `{monitor_min}` min\n"
            "  → Notifica quando o site *cair* ou *voltar*\n"
            "  → Quando voltar, já dispara uma busca imediatamente\n\n"
            f"🔍 *Busca* — varredura completa a cada `{busca_h}` h\n"
            "  → Só executa se o site estiver online\n\n"
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
                "Não há tarefas automáticas ativas\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        await update.message.reply_text(
            "🛑 *Modo automático desativado\\.*\nUse /auto para reativar\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    return [cmd_checar, cmd_auto, cmd_parar]