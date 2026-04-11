"""
bot/jobs.py
Jobs automáticos do bot: monitor de disponibilidade e busca periódica.

job_monitor  — roda a cada N minutos, faz ping leve no site.
               Notifica quando o site cai ou volta.
               Quando volta, só dispara busca imediata se o site esteve
               offline por tempo suficiente para justificá-la (≥ MIN_OFFLINE_PARA_BUSCA_MIN).

job_busca    — roda a cada N horas, faz varredura completa de editais.
               Ignorado silenciosamente se o site estiver offline.
"""

from __future__ import annotations

import logging
import re

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.database import get_user, registrar_disponibilidade, tempo_offline
from bot.formatters import esc, enviar_resultado_busca
from bot.scraper import checar_site, buscar_novos_editais

logger = logging.getLogger(__name__)

# Tempo mínimo offline (em minutos) para justificar uma busca ao site voltar.
# Se o site ficou fora por menos que isso, provavelmente foi um 503 transitório
# e não há editais novos para encontrar.
MIN_OFFLINE_PARA_BUSCA_MIN = 60


# ─────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────


def _settings_do_job(context: ContextTypes.DEFAULT_TYPE):
    """Extrai o objeto Settings passado via job.data no /auto."""
    return context.job.data["settings"]


async def _send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, **kwargs) -> None:
    """Atalho para enviar mensagem num job (sem update disponível)."""
    await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)


def _duracao_em_minutos(duracao_str: str) -> int:
    """
    Converte a string de duração produzida por _calcular_duracao() para minutos.
    Formatos suportados: "5min", "1h30min", "2d 3h15min".
    Retorna 0 se não conseguir parsear.
    """
    total = 0
    dias  = re.search(r"(\d+)d", duracao_str)
    horas = re.search(r"(\d+)h", duracao_str)
    mins  = re.search(r"(\d+)min", duracao_str)
    if dias:  total += int(dias.group(1)) * 1440
    if horas: total += int(horas.group(1)) * 60
    if mins:  total += int(mins.group(1))
    return total


# ─────────────────────────────────────────────
# Job — monitor de disponibilidade
# ─────────────────────────────────────────────


async def job_monitor(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ping leve no site. Notifica apenas quando o estado muda.
    Se o site voltou após uma queda longa (≥ MIN_OFFLINE_PARA_BUSCA_MIN),
    dispara busca imediata. Quedas curtas/transitórias apenas notificam.
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
    """
    Notifica que o site voltou.
    Só dispara busca completa se o site esteve offline por tempo relevante
    (≥ MIN_OFFLINE_PARA_BUSCA_MIN), evitando varreduras desnecessárias após
    instabilidades passageiras de poucos minutos.
    """
    user = get_user(chat_id, settings.config_padrao())

    offline_por = ""
    deve_buscar = False

    if user.historico_disponibilidade:
        ultimo = user.historico_disponibilidade[-1]
        if ultimo.evento == "voltou" and ultimo.ficou_offline_por:
            offline_por = f"\nEstava offline há {esc(ultimo.ficou_offline_por)}\\."
            minutos_offline = _duracao_em_minutos(ultimo.ficou_offline_por)
            deve_buscar = minutos_offline >= MIN_OFFLINE_PARA_BUSCA_MIN
            logger.info(
                "Site voltou após %d min offline (chat_id=%s). Busca: %s",
                minutos_offline, chat_id, "sim" if deve_buscar else "não",
            )

    if deve_buscar:
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

    else:
        # Queda curta/transitória: só notifica o retorno, sem busca
        await _send(
            context,
            chat_id,
            f"🟢 *O site do SENAI\\-PE voltou\\!*{offline_por}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


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
