"""
bot/commands/config.py
Comandos de configuração por usuário:
/config, /addtermo, /rmtermo, /termos, /resetconfig
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

        # /config cidade <valor>
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
        """
        /addtermo <termo>  →  adiciona termo à lista do usuário.
        """
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
        """
        /rmtermo <termo>  →  remove termo da lista do usuário.
        """
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

        # Busca case-insensitive na lista
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
        """
        /termos  →  lista todos os termos ativos do usuário.
        """
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
        """
        /resetconfig  →  restaura cidade e termos para os padrões do config.yaml.
        """
        chat_id = update.effective_chat.id
        padrao  = settings.config_padrao()

        atualizar_config(
            chat_id,
            cidade=padrao.cidade,
            termos=list(padrao.termos),
            intervalo_monitor=padrao.intervalo_monitor,
            intervalo_busca=padrao.intervalo_busca,
        )

        await update.message.reply_text(
            f"♻️ *Configuração restaurada para os padrões\\!*\n\n"
            f"📍 Cidade: `{esc(padrao.cidade)}`\n"
            f"🏷 Termos: `{len(padrao.termos)}` termos padrão\n\n"
            f"Use /config para conferir\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    return [cmd_config, cmd_addtermo, cmd_rmtermo, cmd_termos, cmd_resetconfig]