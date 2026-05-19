"""
bot/scheduler.py
Helper central para criar/recriar jobs de busca e monitor no job_queue.

Centraliza a lógica de escolha entre run_repeating e run_daily,
evitando duplicação entre cmd_auto, cmd_horario e _restaurar_jobs.
"""

from __future__ import annotations

import logging
from datetime import time as dt_time

from telegram.ext import Application

logger = logging.getLogger(__name__)


def _parse_horario(horario_str: str) -> dt_time:
    """
    Converte "HH:MM" para datetime.time.
    Levanta ValueError se o formato for inválido.
    """
    partes = horario_str.strip().split(":")
    if len(partes) != 2:
        raise ValueError(f"Formato inválido: '{horario_str}'. Use HH:MM.")
    hora, minuto = int(partes[0]), int(partes[1])
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        raise ValueError(f"Hora ou minuto fora do intervalo: {hora}:{minuto:02d}")
    return dt_time(hora, minuto)


def agendar_jobs(
    app: Application,
    chat_id: int,
    settings,
    *,
    first_monitor: int = 15,
    first_busca: int = 30,
) -> dict:
    """
    Cancela os jobs existentes do usuário e recria com a configuração atual.

    Retorna um dict com metadados úteis para a mensagem de confirmação:
      {
        "modo_busca": "horario" | "intervalo",
        "horario": "HH:MM" | None,
        "intervalo_h": int | None,
        "intervalo_monitor_min": int,
      }
    """
    from bot.database import get_user
    from bot.jobs import job_monitor, job_busca

    user = get_user(chat_id, settings.config_padrao())

    # ── Cancela jobs anteriores ──────────────────────────────────────────
    for nome in (f"monitor_{chat_id}", f"busca_{chat_id}"):
        for job in app.job_queue.get_jobs_by_name(nome):
            job.schedule_removal()
            logger.debug("Job cancelado: %s", nome)

    # ── Monitor — sempre por intervalo fixo ──────────────────────────────
    app.job_queue.run_repeating(
        job_monitor,
        interval=user.config.intervalo_monitor,
        first=first_monitor,
        chat_id=chat_id,
        name=f"monitor_{chat_id}",
        data={"settings": settings},
    )

    # ── Busca — por horário fixo ou intervalo ────────────────────────────
    horario_str = user.config.horario_busca
    if horario_str:
        horario = _parse_horario(horario_str)
        app.job_queue.run_daily(
            job_busca,
            time=horario,
            chat_id=chat_id,
            name=f"busca_{chat_id}",
            data={"settings": settings},
        )
        logger.info(
            "Job de busca agendado diariamente às %s para chat_id=%s",
            horario_str, chat_id,
        )
        return {
            "modo_busca": "horario",
            "horario": horario_str,
            "intervalo_h": None,
            "intervalo_monitor_min": user.config.intervalo_monitor // 60,
        }
    else:
        app.job_queue.run_repeating(
            job_busca,
            interval=user.config.intervalo_busca,
            first=first_busca,
            chat_id=chat_id,
            name=f"busca_{chat_id}",
            data={"settings": settings},
        )
        logger.info(
            "Job de busca agendado a cada %ds para chat_id=%s",
            user.config.intervalo_busca, chat_id,
        )
        return {
            "modo_busca": "intervalo",
            "horario": None,
            "intervalo_h": user.config.intervalo_busca // 3600,
            "intervalo_monitor_min": user.config.intervalo_monitor // 60,
        }
