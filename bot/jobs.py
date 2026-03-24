"""
bot/jobs.py
Jobs automáticos do bot: monitor de disponibilidade e busca periódica.

job_monitor  — roda a cada N minutos, faz ping leve no site.
               Notifica quando o site cai ou volta.
               Quando volta, dispara busca imediata.

job_busca    — roda a cada N horas, faz varredura completa de editais.
               Ignorado silenciosamente se o site estiver offline.
"""

from __future__ import annotations

import logging

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.database import get_user, registrar_disponibilidade, tempo_offline
from bot.formatters import esc, enviar_resultado_busca
from bot.scraper import checar_site, buscar_novos_editais

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────


def _settings_do_job(context: ContextTypes.DEFAULT_TYPE):
    """Extrai o objeto Settings passado via job.data no /auto."""
    return context.job.data["settings"]


async def _send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, **kwargs) -> None:
    """Atalho para enviar mensagem num job (sem update disponível)."""
    await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)


# ─────────────────────────────────────────────
# Job — monitor de disponibilidade
# ─────────────────────────────────────────────


async def job_monitor(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ping leve no site. Notifica apenas quando o estado muda.
    Se o site voltou, dispara busca imediata.
    """
    chat_id  = context.job.chat_id
    settings = _settings_do_job(context)

    online, motivo = await checar_site(settings.url_editais)
    mudou = registrar_disponibilidade(chat_id, online)

    if not mudou:
        return  # estado não mudou — silêncio total

    if online:
        await _notificar_voltou(context, chat_id, settings)
    else:
        await _notificar_caiu(context, chat_id, motivo)


async def _notificar_voltou(context, chat_id: int, settings) -> None:
    """Notifica que o site voltou e dispara busca imediata."""
    user = get_user(chat_id, settings.config_padrao())

    # Recupera duração do período offline do histórico
    offline_por = ""
    if user.historico_disponibilidade:
        ultimo = user.historico_disponibilidade[-1]
        if ultimo.evento == "voltou" and ultimo.ficou_offline_por:
            offline_por = f"\nEstava offline há {esc(ultimo.ficou_offline_por)}\\."

    logger.info("Site voltou. Disparando busca imediata para chat_id=%s", chat_id)

    await _send(
        context,
        chat_id,
        f"🟢 *O site do SENAI\\-PE voltou\\!*"
        f"{offline_por}\n\n"
        f"🔍 Iniciando busca de editais automaticamente\\.\\.\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    resultado = await buscar_novos_editais(
        chat_id=chat_id,
        user=user,
        url_editais=settings.url_editais,
        timeout=settings.request_timeout,
    )

    async def send(txt, **kw):
        await _send(context, chat_id, txt, **kw)

    await enviar_resultado_busca(send, resultado, "🟢")


async def _notificar_caiu(context, chat_id: int, motivo: str) -> None:
    """Notifica que o site ficou offline."""
    await _send(
        context,
        chat_id,
        f"🔴 *Site do SENAI\\-PE ficou offline\\!*\n\n"
        f"Motivo: {esc(motivo)}\n"
        f"Você será notificado quando voltar\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ─────────────────────────────────────────────
# Job — busca periódica
# ─────────────────────────────────────────────


async def job_busca(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Varredura completa de editais. Executa a cada N horas.
    Ignorado silenciosamente se o site estiver offline —
    o job_monitor vai disparar a busca quando o site voltar.
    """
    chat_id  = context.job.chat_id
    settings = _settings_do_job(context)

    logger.info("Job de busca periódica para chat_id=%s", chat_id)

    online, _ = await checar_site(settings.url_editais)
    registrar_disponibilidade(chat_id, online)

    if not online:
        user = get_user(chat_id, settings.config_padrao())
        logger.info(
            "Busca periódica ignorada: site offline há %s (chat_id=%s)",
            tempo_offline(user),
            chat_id,
        )
        return

    try:
        user = get_user(chat_id, settings.config_padrao())

        resultado = await buscar_novos_editais(
            chat_id=chat_id,
            user=user,
            url_editais=settings.url_editais,
            timeout=settings.request_timeout,
        )

        async def send(txt, **kw):
            await _send(context, chat_id, txt, **kw)

        await enviar_resultado_busca(send, resultado, "⏰")

    except Exception as exc:
        logger.error("Erro no job de busca periódica (chat_id=%s): %s", chat_id, exc)
        await _send(
            context,
            chat_id,
            f"⚠️ Erro na busca automática: {esc(str(exc))}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )