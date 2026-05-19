"""
bot/commands/config.py
Comandos de configuração por usuário:
/config, /addtermo, /rmtermo, /termos, /resetconfig, /horario
"""

from __future__ import annotations

from unidecode import unidecode

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.database import get_user, atualizar_config, salvar_user
from bot.formatters import esc, formatar_config
from config import Settings


def _normalizar(texto: str) -> str:
    return unidecode(texto.strip().lower())


def setup(settings: Settings):
    """Retorna os handlers deste módulo. Chamado pelo main.py."""

    async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Sem argumentos  → exibe configuração atual.
        /config cidade <valor>  → altera a cidade.
        """
        chat_id = update.effective_chat.id
        user    = get_user(chat_id, settings.config_padrao())
        args    = context.args or []

        if not args:
            await update.message.reply_text(
                formatar_config(user),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        if len(args) >= 2 and args[0].lower() == "cidade":
            nova_cidade = _normalizar(" ".join(args[1:]))
            if not nova_cidade:
                await update.message.reply_text(
                    "⚠️ Informe o nome da cidade\\. Exemplo: `/config cidade recife`",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                return

            atualizar_config(chat_id, cidade=nova_cidade)
            await update.message.reply_text(
                f"✅ Cidade atualizada para `{esc(nova_cidade)}`\\.\n"
                f"Use /buscar para buscar com a nova configuração\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        await update.message.reply_text(
            "⚠️ Uso correto:\n"
            "`/config` — ver configuração\n"
            "`/config cidade <nome>` — alterar cidade",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    async def cmd_addtermo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        args    = context.args or []

        if not args:
            await update.message.reply_text(
                "⚠️ Informe o termo\\. Exemplo: `/addtermo machine learning`",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        novo_termo = _normalizar(" ".join(args))
        user = get_user(chat_id, settings.config_padrao())

        if novo_termo in [_normalizar(t) for t in user.config.termos]:
            await update.message.reply_text(
                f"ℹ️ O termo `{esc(novo_termo)}` já existe na sua lista\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        user.config.termos.append(novo_termo)
        salvar_user(chat_id, user)

        await update.message.reply_text(
            f"✅ Termo `{esc(novo_termo)}` adicionado\\.\n"
            f"Sua lista agora tem `{len(user.config.termos)}` termo\\(s\\)\\.\n\n"
            f"💡 Use /forcar para re\\-analisar editais já rejeitados com o novo termo\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    async def cmd_rmtermo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        args    = context.args or []

        if not args:
            await update.message.reply_text(
                "⚠️ Informe o termo\\. Exemplo: `/rmtermo ads`",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        termo_alvo = _normalizar(" ".join(args))
        user = get_user(chat_id, settings.config_padrao())

        termo_na_lista = next(
            (t for t in user.config.termos if _normalizar(t) == termo_alvo), None
        )

        if not termo_na_lista:
            await update.message.reply_text(
                f"⚠️ Termo `{esc(termo_alvo)}` não encontrado na sua lista\\.\n"
                f"Use /termos para ver os termos ativos\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        user.config.termos.remove(termo_na_lista)
        salvar_user(chat_id, user)

        await update.message.reply_text(
            f"🗑 Termo `{esc(termo_na_lista)}` removido\\.\n"
            f"Sua lista agora tem `{len(user.config.termos)}` termo\\(s\\)\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    async def cmd_termos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        user    = get_user(chat_id, settings.config_padrao())

        if not user.config.termos:
            await update.message.reply_text(
                "📭 Você não tem termos configurados\\.\n"
                "Use /addtermo para adicionar\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        lista = "\n".join(f"  `{esc(t)}`" for t in sorted(user.config.termos))
        await update.message.reply_text(
            f"🏷 *Seus termos ativos \\({len(user.config.termos)}\\):*\n\n{lista}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    async def cmd_resetconfig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        padrao  = settings.config_padrao()

        atualizar_config(
            chat_id,
            cidade=padrao.cidade,
            termos=list(padrao.termos),
            intervalo_monitor=padrao.intervalo_monitor,
            intervalo_busca=padrao.intervalo_busca,
            horario_busca=None,   # remove horário fixo ao resetar
        )

        await update.message.reply_text(
            f"♻️ *Configuração restaurada para os padrões\\!*\n\n"
            f"📍 Cidade: `{esc(padrao.cidade)}`\n"
            f"🏷 Termos: `{len(padrao.termos)}` termos padrão\n"
            f"🕐 Horário de busca: intervalo padrão \\(sem horário fixo\\)\n\n"
            f"Use /config para conferir\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    async def cmd_horario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        /horario         → exibe o horário atual de busca
        /horario HH:MM   → define horário fixo diário para a busca automática
        /horario off     → remove o horário fixo (volta para intervalo)

        Quando o modo automático já estiver ativo, recria o job de busca
        imediatamente com a nova configuração — sem precisar /parar + /auto.
        """
        from bot.scheduler import agendar_jobs, _parse_horario

        chat_id = update.effective_chat.id
        args    = context.args or []
        user    = get_user(chat_id, settings.config_padrao())

        # ── Sem argumentos: exibe configuração atual ─────────────────────
        if not args:
            if user.config.horario_busca:
                msg = (
                    f"🕐 *Horário de busca atual:* `{esc(user.config.horario_busca)}`\n\n"
                    f"A busca automática roda todos os dias nesse horário\\.\n"
                    f"Use `/horario off` para voltar ao modo por intervalo\\."
                )
            else:
                busca_h = user.config.intervalo_busca // 3600
                msg = (
                    f"🕐 *Horário de busca:* não definido\n\n"
                    f"A busca roda a cada `{busca_h}h` a partir do momento em que "
                    f"o `/auto` foi ativado\\.\n"
                    f"Use `/horario HH:MM` para fixar um horário diário\\."
                )
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
            return

        # ── /horario off: remove horário fixo ───────────────────────────
        if args[0].lower() == "off":
            atualizar_config(chat_id, horario_busca=None)

            auto_ativo = bool(
                context.job_queue.get_jobs_by_name(f"monitor_{chat_id}") or
                context.job_queue.get_jobs_by_name(f"busca_{chat_id}")
            )
            if auto_ativo:
                agendar_jobs(context.application, chat_id, settings)

            busca_h = user.config.intervalo_busca // 3600
            sufixo = " O job foi atualizado automaticamente\\." if auto_ativo else ""
            await update.message.reply_text(
                f"✅ Horário fixo removido\\.\n"
                f"A busca voltará a rodar a cada `{busca_h}h` \\(modo intervalo\\)\\."
                f"{sufixo}",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        # ── /horario HH:MM: define horário ──────────────────────────────
        horario_str = args[0].strip()
        try:
            _parse_horario(horario_str)   # valida formato e intervalo
        except ValueError as exc:
            await update.message.reply_text(
                f"⚠️ Horário inválido: `{esc(str(exc))}`\n\n"
                f"Exemplos válidos: `/horario 08:00` `/horario 22:30`",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return

        atualizar_config(chat_id, horario_busca=horario_str)

        # Se o modo automático já está ativo, recria o job de busca agora
        auto_ativo = bool(
            context.job_queue.get_jobs_by_name(f"monitor_{chat_id}") or
            context.job_queue.get_jobs_by_name(f"busca_{chat_id}")
        )
        if auto_ativo:
            agendar_jobs(context.application, chat_id, settings)
            sufixo = "\n\n✅ O job de busca foi reagendado agora mesmo\\."
        else:
            sufixo = (
                "\n\n💡 Use /auto para ativar o modo automático "
                "e o bot já passará a usar esse horário\\."
            )

        await update.message.reply_text(
            f"🕐 *Horário de busca definido:* `{esc(horario_str)}`\n\n"
            f"A partir de agora a busca automática rodará todos os dias "
            f"às `{esc(horario_str)}`\\."
            f"{sufixo}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    return [cmd_config, cmd_addtermo, cmd_rmtermo, cmd_termos, cmd_resetconfig, cmd_horario]
