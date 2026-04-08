"""
bot/formatters.py
Helpers de formatação e envio de mensagens no Telegram.
Nenhuma lógica de negócio aqui — só apresentação.
"""

from __future__ import annotations

import time
import asyncio
import logging
from typing import Callable, Awaitable

from telegram import Message
from telegram.constants import ParseMode

from bot.database import Edital, UserData
from bot.database import tempo_offline as _tempo_offline  # re-exportado por conveniência

logger = logging.getLogger(__name__)

# Caracteres reservados do MarkdownV2
_MD_RESERVED = r"\_*[]()~`>#+-=|{}.!"


# ─────────────────────────────────────────────
# Escape
# ─────────────────────────────────────────────


def esc(texto: str | None) -> str:
    """
    Escapa todos os caracteres reservados do MarkdownV2.

    FIX: aceita None explicitamente — vários campos opcionais do Edital
    (motivo, encontrado_em) podem ser None e eram passados diretamente
    ao esc(), causando AttributeError em runtime.
    """
    if texto is None:
        return ""
    for c in _MD_RESERVED:
        texto = texto.replace(c, f"\\{c}")
    return texto


# ─────────────────────────────────────────────
# Formatação de editais
# ─────────────────────────────────────────────


_BADGE_MAP = {
    "titulo":           "📌 título",
    "pdf":              "📄 PDF",
    "reanalise_titulo": "🔄 reanálise",
}


def formatar_edital(edital: Edital) -> str:
    """Formata um edital aceito para exibição no Telegram (MarkdownV2)."""
    badge     = _BADGE_MAP.get(edital.encontrado_em or "", edital.encontrado_em or "?")
    termos    = edital.termos_ti or []
    termos_str = ", ".join(termos[:3]) if termos else "—"

    return (
        f"🎓 *Novo edital encontrado\\!*\n\n"
        f"📋 {esc(edital.titulo)}\n"
        f"🔍 Encontrado em: {esc(badge)}\n"
        f"🏷 Termos: `{esc(termos_str)}`\n\n"
        f"[📥 Abrir PDF]({edital.link})"
    )


def formatar_status(user: UserData, auto_ativo: bool) -> str:
    """Formata o painel /status completo para um usuário."""

    # Site
    if user.site_online is True:
        site_str = "Online ✅"
    elif user.site_online is False:
        site_str = f"Offline ❌ \\(há {esc(_tempo_offline(user))}\\)"
    else:
        site_str = "Desconhecido ❓"

    # Última busca
    ultima = user.ultima_busca_completa or "nunca"
    if ultima != "nunca":
        ultima = ultima[:16].replace("T", " ")

    # Modo automático
    auto_str = "Ativo ✅" if auto_ativo else "Inativo ⏸"

    # Histórico de disponibilidade
    hist_str = ""
    for ev in reversed(user.historico_disponibilidade[-3:]):
        em = ev.em[:16].replace("T", " ")
        if ev.evento == "voltou":
            dur = ev.ficou_offline_por or "?"
            hist_str += (
                f"\n  🟢 Voltou em {esc(em)}"
                f" \\(offline por {esc(dur)}\\)"
            )
        else:
            hist_str += f"\n  🔴 Caiu em {esc(em)}"

    texto = (
        f"📊 *Painel do Bot SENAI*\n\n"
        f"🌐 Site: {site_str}\n"
        f"⚙️ Modo automático: {auto_str}\n"
        f"✅ Aceitos: `{len(user.aceitos)}`\n"
        f"❌ Rejeitados: `{len(user.rejeitados)}`\n"
        f"🔍 Total de buscas: `{user.stats.total_buscas}`\n"
        f"📥 PDFs analisados: `{user.stats.total_pdfs_baixados}`\n"
        f"🕒 Última busca: `{esc(ultima)}`\n"
        f"📍 Cidade: `{esc(user.config.cidade)}`\n"
        f"🏷 Termos ativos: `{len(user.config.termos)}`"
    )

    if hist_str:
        texto += f"\n\n📅 *Histórico recente:*{hist_str}"

    return texto


def formatar_config(user: UserData) -> str:
    """Formata a config atual do usuário para exibição."""
    termos_str = "\n".join(f"  • {esc(t)}" for t in user.config.termos) or "  _nenhum_"
    monitor_min = user.config.intervalo_monitor // 60
    busca_h = user.config.intervalo_busca // 3600

    return (
        f"⚙️ *Sua configuração atual*\n\n"
        f"📍 Cidade: `{esc(user.config.cidade)}`\n"
        f"👁 Monitor: a cada `{monitor_min}` min\n"
        f"🔍 Busca: a cada `{busca_h}` h\n\n"
        f"🏷 *Termos \\({len(user.config.termos)}\\):*\n{termos_str}"
    )


# ─────────────────────────────────────────────
# Helpers de envio
# ─────────────────────────────────────────────


# Tipo de função de envio compatível com reply_text e bot.send_message
SendFn = Callable[..., Awaitable[Message]]


async def enviar_resultado_busca(send_fn: SendFn, resultado: dict, prefixo: str = "🔍") -> None:
    """
    Envia o resumo de uma busca completa.
    Funciona com reply_text (comandos) e bot.send_message (jobs automáticos).
    """
    novos         = resultado["novos_aceitos"]
    total_site    = resultado["total_site"]
    ja_conhecidos = resultado["ja_conhecidos"]

    if novos:
        await send_fn(
            f"{prefixo} *{esc(str(len(novos)))} novo\\(s\\) edital\\(is\\) de TI encontrado\\(s\\)\\!*",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        for edital in novos:
            await send_fn(
                formatar_edital(edital),
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )
    else:
        await send_fn(
            f"{prefixo} *Busca concluída\\.* Nenhum edital novo de TI\\.\n"
            f"📊 {esc(str(total_site))} no site, "
            f"{esc(str(ja_conhecidos))} já conhecidos\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


async def atualizar_progresso(
    update,
    linhas: list[str],
    msg_id: int | None,
    last_update: list[float],
    intervalo: float = 2.0,
) -> int:
    """
    Edita (ou cria) uma mensagem de progresso no Telegram.
    Atualiza no máximo a cada `intervalo` segundos para evitar flood.
    Retorna o message_id da mensagem editada/criada.
    """
    texto = "\n".join(linhas[-20:])
    if len(texto) > 4096:
        texto = "…\n" + texto[-4090:]

    agora = time.time()
    eh_final = any(k in (linhas[-1] if linhas else "") for k in ("Concluído", "⚠️", "concluído"))
    if not eh_final and agora - last_update[0] < intervalo:
        return msg_id or 0

    last_update[0] = agora

    try:
        if msg_id:
            await update.message.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                text=texto,
            )
            return msg_id
    except Exception:
        pass

    msg = await update.message.reply_text(texto)
    return msg.message_id
