"""
config.py
Carrega e valida todas as configurações da aplicação.

Hierarquia (maior prioridade primeiro):
  1. Variáveis de ambiente / .env  →  credenciais e overrides de infra
  2. config.yaml                   →  padrões globais do bot
  3. Valores hardcoded aqui        →  fallbacks de segurança
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from bot.database import UserConfig

logger = logging.getLogger(__name__)

load_dotenv()

_YAML_PATH = Path("config.yaml")


# ─────────────────────────────────────────────
# Loader do YAML
# ─────────────────────────────────────────────


def _carregar_yaml() -> dict:
    if not _YAML_PATH.exists():
        logger.warning("config.yaml não encontrado. Usando apenas padrões internos.")
        return {}
    try:
        with _YAML_PATH.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        logger.error("Erro ao ler config.yaml: %s. Usando padrões internos.", exc)
        return {}


# ─────────────────────────────────────────────
# Settings — configurações globais da aplicação
# ─────────────────────────────────────────────


@dataclass
class Settings:
    # ── Credenciais (obrigatórias, vêm do .env) ──
    bot_token: str

    # ── URLs ──
    url_editais: str
    url_portal: str

    # ── Padrões para novos usuários ──
    cidade_padrao: str
    termos_padrao: list[str]
    intervalo_monitor: int
    intervalo_busca: int

    # ── Infra ──
    porta_flask: int
    request_timeout: int
    max_retries: int

    def config_padrao(self) -> UserConfig:
        """Retorna um UserConfig com os padrões do yaml, pronto para novos usuários."""
        return UserConfig(
            cidade=self.cidade_padrao,
            termos=list(self.termos_padrao),
            intervalo_monitor=self.intervalo_monitor,
            intervalo_busca=self.intervalo_busca,
        )


def carregar_settings() -> Settings:
    """
    Constrói o objeto Settings combinando .env e config.yaml.
    Lança ValueError se BOT_TOKEN não estiver definido.
    """
    yaml_cfg = _carregar_yaml()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError(
            "BOT_TOKEN não definido. Adicione ao arquivo .env ou às variáveis de ambiente."
        )

    settings = Settings(
        # Credenciais
        bot_token=token,

        # URLs — .env tem prioridade sobre yaml
        url_editais=os.getenv(
            "URL_EDITAIS",
            yaml_cfg.get("url_editais", "https://www.pe.senai.br/editais/"),
        ),
        url_portal=os.getenv(
            "URL_PORTAL",
            yaml_cfg.get("url_portal", "https://sge.pe.senai.br"),
        ),

        # Padrões de usuário
        cidade_padrao=os.getenv(
            "CIDADE_PADRAO",
            yaml_cfg.get("cidade_padrao", "cabo"),
        ),
        termos_padrao=yaml_cfg.get("termos_padrao", _TERMOS_FALLBACK),
        intervalo_monitor=int(os.getenv(
            "INTERVALO_MONITOR",
            yaml_cfg.get("intervalo_monitor", 300),
        )),
        intervalo_busca=int(os.getenv(
            "INTERVALO_BUSCA",
            yaml_cfg.get("intervalo_busca", 86_400),
        )),

        # Infra
        porta_flask=int(os.getenv("PORT", 10_000)),
        request_timeout=int(os.getenv("REQUEST_TIMEOUT", 30)),
        max_retries=int(os.getenv("MAX_RETRIES", 3)),
    )

    logger.info(
        "Settings carregadas — cidade padrão: '%s', %d termo(s), monitor: %ds, busca: %ds",
        settings.cidade_padrao,
        len(settings.termos_padrao),
        settings.intervalo_monitor,
        settings.intervalo_busca,
    )

    return settings


# ─────────────────────────────────────────────
# Fallback de termos (caso config.yaml suma)
# ─────────────────────────────────────────────

_TERMOS_FALLBACK: list[str] = [
    "desenvolvimento de sistemas",
    "tecnico em desenvolvimento de sistemas",
    "informatica",
    "tecnico em informatica",
    "informatica para internet",
    "redes de computadores",
    "tecnico em redes",
    "programacao",
    "programador",
    "desenvolvimento web",
    "software",
    "banco de dados",
    "seguranca da informacao",
    "ciberseguranca",
    "tecnologia da informacao",
    "ti",
    "suporte tecnico",
    "manutencao de computadores",
    "analise e desenvolvimento",
    "ads",
]