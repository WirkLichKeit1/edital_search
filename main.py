"""
main.py
Ponto de entrada da aplicação.
Responsabilidades:
  - Carregar Settings
  - Iniciar Flask em thread daemon
  - Registrar todos os handlers e comandos do Telegram
  - Restaurar jobs automáticos de usuários que tinham /auto ativo antes do restart
  - Iniciar o bot em modo polling
"""

from __future__ import annotations

import logging
import threading

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler

from bot_config import carregar_settings
from server import iniciar as iniciar_flask

import bot.commands.info    as mod_info
import bot.commands.monitor as mod_monitor
import bot.commands.busca   as mod_busca
import bot.commands.config  as mod_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Menu de comandos do Telegram
# ─────────────────────────────────────────────

_COMANDOS = [
    BotCommand("buscar",      "Buscar novos editais agora"),
    BotCommand("listar",      "Listar editais aceitos"),
    BotCommand("rejeitados",  "Ver editais rejeitados e motivos"),
    BotCommand("forcar",      "Re-analisar editais rejeitados"),
    BotCommand("checar",      "Verificar se o site está online"),
    BotCommand("auto",        "Ativar modo automático (monitor + busca)"),
    BotCommand("parar",       "Desativar modo automático"),
    BotCommand("horario",     "Definir horário fixo diário para a busca"),
    BotCommand("config",      "Ver ou alterar sua configuração"),
    BotCommand("addtermo",    "Adicionar um termo de busca"),
    BotCommand("rmtermo",     "Remover um termo de busca"),
    BotCommand("termos",      "Listar seus termos ativos"),
    BotCommand("resetconfig", "Restaurar configuração padrão"),
    BotCommand("status",      "Painel completo de informações"),
    BotCommand("ajuda",       "Exibir ajuda"),
]


# ─────────────────────────────────────────────
# Registro de handlers
# ─────────────────────────────────────────────

def _registrar_handlers(app: Application, settings) -> None:
    mapeamento = {
        "cmd_start":       "start",
        "cmd_ajuda":       "ajuda",
        "cmd_status":      "status",
        "cmd_checar":      "checar",
        "cmd_auto":        "auto",
        "cmd_parar":       "parar",
        "cmd_buscar":      "buscar",
        "cmd_listar":      "listar",
        "cmd_rejeitados":  "rejeitados",
        "cmd_forcar":      "forcar",
        "cmd_config":      "config",
        "cmd_addtermo":    "addtermo",
        "cmd_rmtermo":     "rmtermo",
        "cmd_termos":      "termos",
        "cmd_resetconfig": "resetconfig",
        "cmd_horario":     "horario",
    }

    modulos = [mod_info, mod_monitor, mod_busca, mod_config]

    for modulo in modulos:
        handlers = modulo.setup(settings)
        for handler in handlers:
            comando = mapeamento.get(handler.__name__)
            if comando:
                app.add_handler(CommandHandler(comando, handler))
                logger.debug("Handler registrado: /%s → %s", comando, handler.__name__)
            else:
                logger.warning("Handler sem mapeamento: %s", handler.__name__)


# ─────────────────────────────────────────────
# Restauração de jobs após restart
# ─────────────────────────────────────────────

def _restaurar_jobs(app: Application, settings) -> None:
    """
    Recria os jobs automáticos para usuários que tinham auto_ativo=True
    antes do processo reiniciar. Usa agendar_jobs() para respeitar
    horario_busca de cada usuário automaticamente.
    """
    from bot.database import listar_users_auto_ativo
    from bot.scheduler import agendar_jobs

    chat_ids = listar_users_auto_ativo()
    if not chat_ids:
        return

    logger.info("Restaurando modo automático para %d usuário(s)...", len(chat_ids))

    for uid in chat_ids:
        chat_id = int(uid)
        info = agendar_jobs(
            app,
            chat_id,
            settings,
            first_monitor=60,   # pequeno delay inicial para o bot estabilizar
            first_busca=120,
        )
        logger.info(
            "  Jobs restaurados para chat_id=%s (busca: %s)",
            uid,
            info["horario"] or f"intervalo {info['intervalo_h']}h",
        )


# ─────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────

def main() -> None:
    settings = carregar_settings()

    threading.Thread(
        target=iniciar_flask,
        args=(settings.porta_flask,),
        daemon=True,
        name="flask-health",
    ).start()

    logger.info("Bot SENAI iniciando...")

    app = Application.builder().token(settings.bot_token).build()

    _registrar_handlers(app, settings)

    async def post_init(a: Application) -> None:
        await a.bot.set_my_commands(_COMANDOS)
        logger.info("%d comando(s) registrado(s) no Telegram.", len(_COMANDOS))
        _restaurar_jobs(a, settings)

    app.post_init = post_init

    logger.info("Polling iniciado.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
