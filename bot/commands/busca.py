"""
bot/commands/busca.py
Comandos de busca e listagem: /buscar, /listar, /rejeitados, /forcar
"""

from __future__ import annotations

import asyncio
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.database import get_user, tempo_offline
from bot.formatters import esc, formatar_edital, enviar_resultado_busca, atualizar_progresso
from bot.scraper import checar_site, buscar_novos_editais, reanalisar_rejeitados
from config import Settings


def setup(settings: Settings):
    """Retorna os handlers deste módulo. Chamado pelo main.py."""

    async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        online, motivo = await checar_site(settings.url_editais)

        user = get_user(chat_id, settings.config_padrao())

        if not online:
            await update.message.reply_text(
                f"❌ *Site indisponível*\n"
                f"Motivo: {esc(motivo)}\n"
                f"Offline há: `{esc(tempo_offline(user))}`\n\n"
                f"Use /auto para ser notificado quando o site voltar\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        # Progresso em tempo real
        log_linhas: list[str] = ["🔍 Iniciando busca..."]
        msg_id: list[int] = [0]
        last_update: list[float] = [time.time()]
        loop = asyncio.get_event_loop()

        async def progresso(linha: str) -> None:
            log_linhas.append(linha)
            msg_id[0] = await atualizar_progresso(
                update, log_linhas, msg_id[0], last_update
            )

        def progresso_sync(linha: str) -> None:
            asyncio.run_coroutine_threadsafe(progresso(linha), loop)

        resultado = await buscar_novos_editais(
            chat_id=chat_id,
            user=user,
            url_editais=settings.url_editais,
            timeout=settings.request_timeout,
            progresso_cb=progresso_sync,
        )

        await asyncio.sleep(0.5)
        await atualizar_progresso(update, log_linhas, msg_id[0], last_update=[0.0])
        await enviar_resultado_busca(update.message.reply_text, resultado, "🔍")

    async def cmd_listar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        user = get_user(chat_id, settings.config_padrao())

        if not user.aceitos:
            await update.message.reply_text(
                "📭 Nenhum edital aceito ainda\\. Use /buscar\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        await update.message.reply_text(
            f"📋 *{len(user.aceitos)} edital\\(is\\) aceito\\(s\\):*",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        for edital in user.aceitos[-20:]:
            await update.message.reply_text(
                formatar_edital(edital),
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )

    async def cmd_rejeitados(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        user = get_user(chat_id, settings.config_padrao())

        if not user.rejeitados:
            await update.message.reply_text(
                "📭 Nenhum edital rejeitado registrado\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        # Agrupa por motivo
        por_motivo: dict[str, list] = {}
        for e in user.rejeitados:
            por_motivo.setdefault(e.motivo or "desconhecido", []).append(e)

        linhas = [f"🗂 *{len(user.rejeitados)} edital\\(is\\) rejeitado\\(s\\):*\n"]
        for motivo, lista in por_motivo.items():
            linhas.append(f"*{esc(motivo)}* \\({len(lista)}\\)")
            for e in lista[-5:]:
                linhas.append(f"• {esc(e.titulo)}")
            linhas.append("")

        await update.message.reply_text(
            "\n".join(linhas),
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )

    async def cmd_forcar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        msg = await update.message.reply_text(
            "🔄 Re\\-analisando rejeitados\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        user = get_user(chat_id, settings.config_padrao())
        resultado = await reanalisar_rejeitados(chat_id, user)
        promovidos = resultado["promovidos"]
        mantidos   = resultado["mantidos"]

        if not promovidos:
            await msg.edit_text(
                f"🔄 Re\\-análise concluída\\. Nenhum promovido\\. {mantidos} mantido\\(s\\)\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        await msg.edit_text(
            f"🎉 *{len(promovidos)} edital\\(is\\) promovido\\(s\\)\\!* "
            f"{mantidos} mantido\\(s\\)\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        for edital in promovidos:
            await update.message.reply_text(
                formatar_edital(edital),
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )

    return [cmd_buscar, cmd_listar, cmd_rejeitados, cmd_forcar]