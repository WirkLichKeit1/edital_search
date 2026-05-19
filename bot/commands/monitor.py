"""
bot/commands/monitor.py
Comandos de monitoramento: /checar, /auto, /parar
"""

from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.database import get_user, registrar_disponibilidade, tempo_offline, set_auto_ativo
from bot.formatters import esc
from bot.scraper import checar_site
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

        # Recarrega para refletir o estado atualizado pelo registrar_disponibilidade
        user = get_user(chat_id, settings.config_padrao())

        offline_info = ""
        if not online_site and user.site_offline_desde:
            offline_info = f"\n⏱ Offline há: `{esc(tempo_offline(user))}`"

        estado_banco = "Online ✅" if user.site_online else (
            "Offline ❌" if user.site_online is False else "Desconhecido ❓"
        )

        texto = (
            f"🌐 *Status dos sistemas SENAI\\-PE*\n\n"
            f"📋 Editais \\(agora\\): {esc(status_site)}\n"
            f"📋 Editais \\(estado consolidado\\): {esc(estado_banco)}\n"
            f"🎓 Portal do aluno: {esc(status_portal)}"
            f"{offline_info}\n\n"
            f"🕒 Verificado agora\\.\n"
            f"_O estado consolidado muda após {settings.limiar_falhas} falhas seguidas\\._"
        )

        if mudou and online_site:
            texto += "\n\n🎉 *O site voltou\\!* Use /buscar\\."

        await msg.edit_text(texto, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from bot.scheduler import agendar_jobs

        chat_id = update.effective_chat.id

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

        info = agendar_jobs(context.application, chat_id, settings)
        set_auto_ativo(chat_id, True)

        monitor_min = info["intervalo_monitor_min"]

        if info["modo_busca"] == "horario":
            busca_linha = (
                f"🔍 *Busca* — todos os dias às `{esc(info['horario'])}`\n"
                f"  → Só executa se o site estiver online"
            )
        else:
            busca_h = info["intervalo_h"]
            busca_linha = (
                f"🔍 *Busca* — varredura completa a cada `{busca_h}` h\n"
                f"  → Só executa se o site estiver online"
            )

        await update.message.reply_text(
            "⚙️ *Modo automático ativado\\!*\n\n"
            f"👁 *Monitor* — verifica o site a cada `{monitor_min}` min\n"
            "  → Notifica quando o site *cair* ou *voltar*\n"
            "  → Quando voltar, já dispara uma busca imediatamente\n\n"
            f"{busca_linha}\n\n"
            "Use /parar para desativar\\.\n"
            "Use /horario para configurar um horário fixo de busca\\.",
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

        set_auto_ativo(chat_id, False)

        await update.message.reply_text(
            "🛑 *Modo automático desativado\\.*\nUse /auto para reativar\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    return [cmd_checar, cmd_auto, cmd_parar]
