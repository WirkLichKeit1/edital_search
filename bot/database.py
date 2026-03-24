"""
bot/database.py
Modelos de dados tipados e todas as operações de persistência.
O banco é um único arquivo JSON com dados separados por chat_id.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ARQUIVO_DB = Path("data/db.json")


# ─────────────────────────────────────────────
# Modelos
# ─────────────────────────────────────────────


@dataclass
class Edital:
    titulo: str
    link: str
    aceito_em: Optional[str] = None
    rejeitado_em: Optional[str] = None
    encontrado_em: Optional[str] = None   # "titulo" | "pdf" | "reanalise_titulo"
    termos_ti: list[str] = field(default_factory=list)
    motivo: Optional[str] = None          # motivo de rejeição

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @staticmethod
    def from_dict(d: dict) -> "Edital":
        return Edital(
            titulo=d["titulo"],
            link=d["link"],
            aceito_em=d.get("aceito_em"),
            rejeitado_em=d.get("rejeitado_em"),
            encontrado_em=d.get("encontrado_em"),
            termos_ti=d.get("termos_ti", []),
            motivo=d.get("motivo"),
        )


@dataclass
class UserConfig:
    """Configurações individuais por usuário. Padrões vêm do config.yaml."""
    cidade: str
    termos: list[str] = field(default_factory=list)
    intervalo_monitor: int = 300      # segundos
    intervalo_busca: int = 86_400     # segundos

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "UserConfig":
        return UserConfig(
            cidade=d["cidade"],
            termos=d.get("termos", []),
            intervalo_monitor=d.get("intervalo_monitor", 300),
            intervalo_busca=d.get("intervalo_busca", 86_400),
        )


@dataclass
class UserStats:
    total_buscas: int = 0
    total_pdfs_baixados: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "UserStats":
        return UserStats(
            total_buscas=d.get("total_buscas", 0),
            total_pdfs_baixados=d.get("total_pdfs_baixados", 0),
        )


@dataclass
class EventoDisponibilidade:
    evento: str                          # "voltou" | "caiu"
    em: str                              # ISO datetime
    ficou_offline_por: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @staticmethod
    def from_dict(d: dict) -> "EventoDisponibilidade":
        return EventoDisponibilidade(
            evento=d["evento"],
            em=d["em"],
            ficou_offline_por=d.get("ficou_offline_por"),
        )


@dataclass
class UserData:
    """Todos os dados de um usuário."""
    config: UserConfig
    aceitos: list[Edital] = field(default_factory=list)
    rejeitados: list[Edital] = field(default_factory=list)
    stats: UserStats = field(default_factory=UserStats)
    site_online: Optional[bool] = None
    site_offline_desde: Optional[str] = None
    ultima_busca_completa: Optional[str] = None
    historico_disponibilidade: list[EventoDisponibilidade] = field(default_factory=list)

    def links_conhecidos(self) -> set[str]:
        return {e.link for e in self.aceitos + self.rejeitados}

    def to_dict(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "aceitos": [e.to_dict() for e in self.aceitos],
            "rejeitados": [e.to_dict() for e in self.rejeitados],
            "stats": self.stats.to_dict(),
            "site_online": self.site_online,
            "site_offline_desde": self.site_offline_desde,
            "ultima_busca_completa": self.ultima_busca_completa,
            "historico_disponibilidade": [ev.to_dict() for ev in self.historico_disponibilidade],
        }

    @staticmethod
    def from_dict(d: dict) -> "UserData":
        return UserData(
            config=UserConfig.from_dict(d["config"]),
            aceitos=[Edital.from_dict(e) for e in d.get("aceitos", [])],
            rejeitados=[Edital.from_dict(e) for e in d.get("rejeitados", [])],
            stats=UserStats.from_dict(d.get("stats", {})),
            site_online=d.get("site_online"),
            site_offline_desde=d.get("site_offline_desde"),
            ultima_busca_completa=d.get("ultima_busca_completa"),
            historico_disponibilidade=[
                EventoDisponibilidade.from_dict(ev)
                for ev in d.get("historico_disponibilidade", [])
            ],
        )


# ─────────────────────────────────────────────
# Persistência
# ─────────────────────────────────────────────


def _carregar_raw() -> dict:
    """Lê o JSON bruto do disco. Retorna dict vazio se não existir ou estiver corrompido."""
    if not ARQUIVO_DB.exists():
        return {"users": {}}
    try:
        return json.loads(ARQUIVO_DB.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("DB corrompido. Criando novo.")
        return {"users": {}}


def _salvar_raw(raw: dict) -> None:
    ARQUIVO_DB.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_DB.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")


def get_user(chat_id: int | str, config_padrao: UserConfig) -> UserData:
    """
    Retorna os dados do usuário. Se não existir, cria com os padrões do config.yaml.
    Nunca retorna None — garante que o usuário sempre existe no banco após essa chamada.
    """
    raw = _carregar_raw()
    uid = str(chat_id)

    if uid not in raw["users"]:
        logger.info("Novo usuário criado: %s", uid)
        user = UserData(config=config_padrao)
        raw["users"][uid] = user.to_dict()
        _salvar_raw(raw)
        return user

    return UserData.from_dict(raw["users"][uid])


def salvar_user(chat_id: int | str, user: UserData) -> None:
    """Persiste os dados de um usuário no banco."""
    raw = _carregar_raw()
    raw["users"][str(chat_id)] = user.to_dict()
    _salvar_raw(raw)


def atualizar_config(chat_id: int | str, **kwargs) -> UserData:
    """
    Atualiza campos da UserConfig de um usuário e persiste.
    Exemplo: atualizar_config(chat_id, cidade="recife")
    """
    raw = _carregar_raw()
    uid = str(chat_id)
    user = UserData.from_dict(raw["users"][uid])

    for chave, valor in kwargs.items():
        if hasattr(user.config, chave):
            setattr(user.config, chave, valor)
        else:
            logger.warning("Campo desconhecido em UserConfig: %s", chave)

    raw["users"][uid] = user.to_dict()
    _salvar_raw(raw)
    return user


# ─────────────────────────────────────────────
# Operações de editais
# ─────────────────────────────────────────────


def adicionar_aceito(chat_id: int | str, edital: Edital) -> None:
    raw = _carregar_raw()
    uid = str(chat_id)
    raw["users"][uid]["aceitos"].append(edital.to_dict())
    _salvar_raw(raw)


def adicionar_rejeitado(chat_id: int | str, edital: Edital) -> None:
    raw = _carregar_raw()
    uid = str(chat_id)
    raw["users"][uid]["rejeitados"].append(edital.to_dict())
    _salvar_raw(raw)


def promover_rejeitado(chat_id: int | str, edital: Edital) -> None:
    """Move um edital de rejeitados para aceitos."""
    raw = _carregar_raw()
    uid = str(chat_id)

    raw["users"][uid]["rejeitados"] = [
        e for e in raw["users"][uid]["rejeitados"] if e["link"] != edital.link
    ]
    edital.motivo = None
    edital.rejeitado_em = None
    raw["users"][uid]["aceitos"].append(edital.to_dict())
    _salvar_raw(raw)


# ─────────────────────────────────────────────
# Disponibilidade
# ─────────────────────────────────────────────


def _calcular_duracao(desde_iso: str) -> str:
    delta = datetime.now() - datetime.fromisoformat(desde_iso)
    total = int(delta.total_seconds())
    dias = total // 86400
    horas = (total % 86400) // 3600
    minutos = (total % 3600) // 60
    if dias > 0:
        return f"{dias}d {horas}h{minutos:02d}min"
    if horas > 0:
        return f"{horas}h{minutos:02d}min"
    return f"{minutos}min"


def tempo_offline(user: UserData) -> str:
    return _calcular_duracao(user.site_offline_desde) if user.site_offline_desde else "desconhecido"


def registrar_disponibilidade(chat_id: int | str, online: bool) -> bool:
    """
    Atualiza o estado de disponibilidade do usuário.
    Retorna True se o estado MUDOU (site caiu ou voltou).
    """
    raw = _carregar_raw()
    uid = str(chat_id)
    user = UserData.from_dict(raw["users"][uid])

    estava_online = user.site_online
    user.site_online = online

    mudou = False

    if online and estava_online is False:
        offline_desde = user.site_offline_desde
        duracao = _calcular_duracao(offline_desde) if offline_desde else "?"
        user.site_offline_desde = None
        user.historico_disponibilidade.append(EventoDisponibilidade(
            evento="voltou",
            em=datetime.now().isoformat(),
            ficou_offline_por=duracao,
        ))
        mudou = True

    elif not online and estava_online is not False:
        user.site_offline_desde = datetime.now().isoformat()
        user.historico_disponibilidade.append(EventoDisponibilidade(
            evento="caiu",
            em=datetime.now().isoformat(),
        ))
        mudou = True

    raw["users"][uid] = user.to_dict()
    _salvar_raw(raw)
    return mudou