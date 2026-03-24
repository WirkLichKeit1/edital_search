"""
tests/test_database.py
Testes para bot/database.py.
Usa tmp_path do pytest para isolar o banco em diretório temporário.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime

import bot.database as db_module
from bot.database import (
    Edital,
    UserConfig,
    UserStats,
    UserData,
    EventoDisponibilidade,
    get_user,
    salvar_user,
    atualizar_config,
    adicionar_aceito,
    adicionar_rejeitado,
    promover_rejeitado,
    registrar_disponibilidade,
    tempo_offline,
)


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def banco_temporario(tmp_path, monkeypatch):
    """Redireciona ARQUIVO_DB para um diretório temporário em cada teste."""
    arquivo = tmp_path / "db.json"
    monkeypatch.setattr(db_module, "ARQUIVO_DB", arquivo)
    return arquivo


@pytest.fixture
def config_padrao() -> UserConfig:
    return UserConfig(
        cidade="cabo",
        termos=["informatica", "desenvolvimento de sistemas"],
        intervalo_monitor=300,
        intervalo_busca=86400,
    )


@pytest.fixture
def edital_ti() -> Edital:
    return Edital(
        titulo="Edital 001/2024 - Cabo TI",
        link="https://exemplo.com/edital001.pdf",
        aceito_em=datetime.now().isoformat(),
        encontrado_em="titulo",
        termos_ti=["informatica"],
    )


@pytest.fixture
def edital_rejeitado() -> Edital:
    return Edital(
        titulo="Edital 002/2024 - Cabo Gastronomia",
        link="https://exemplo.com/edital002.pdf",
        rejeitado_em=datetime.now().isoformat(),
        motivo="sem termos de TI (título e PDF verificados)",
    )


# ─────────────────────────────────────────────
# Modelos — serialização
# ─────────────────────────────────────────────

class TestEditalSerializacao:
    def test_to_dict_sem_campos_none(self, edital_ti):
        d = edital_ti.to_dict()
        assert "motivo" not in d
        assert "rejeitado_em" not in d
        assert d["titulo"] == edital_ti.titulo

    def test_roundtrip(self, edital_ti):
        assert Edital.from_dict(edital_ti.to_dict()) == edital_ti

    def test_roundtrip_rejeitado(self, edital_rejeitado):
        assert Edital.from_dict(edital_rejeitado.to_dict()) == edital_rejeitado


class TestUserDataSerializacao:
    def test_roundtrip_vazio(self, config_padrao):
        user = UserData(config=config_padrao)
        assert UserData.from_dict(user.to_dict()).config.cidade == "cabo"

    def test_roundtrip_com_editais(self, config_padrao, edital_ti, edital_rejeitado):
        user = UserData(
            config=config_padrao,
            aceitos=[edital_ti],
            rejeitados=[edital_rejeitado],
        )
        restaurado = UserData.from_dict(user.to_dict())
        assert len(restaurado.aceitos) == 1
        assert len(restaurado.rejeitados) == 1
        assert restaurado.aceitos[0].titulo == edital_ti.titulo


# ─────────────────────────────────────────────
# get_user / salvar_user
# ─────────────────────────────────────────────

class TestGetUser:
    def test_cria_usuario_novo(self, config_padrao):
        user = get_user(123, config_padrao)
        assert user.config.cidade == "cabo"
        assert user.aceitos == []

    def test_usuario_persistido(self, config_padrao):
        get_user(123, config_padrao)
        user2 = get_user(123, config_padrao)
        # Segunda chamada lê do banco, não recria
        assert user2.config.cidade == "cabo"

    def test_usuarios_isolados(self, config_padrao):
        u1 = get_user(111, config_padrao)
        u2 = get_user(222, config_padrao)
        u1.config.cidade = "recife"
        salvar_user(111, u1)

        u1_recarregado = get_user(111, config_padrao)
        u2_recarregado = get_user(222, config_padrao)
        assert u1_recarregado.config.cidade == "recife"
        assert u2_recarregado.config.cidade == "cabo"  # não foi afetado

    def test_chat_id_como_string_ou_int(self, config_padrao):
        get_user("999", config_padrao)
        user = get_user(999, config_padrao)   # int deve achar o mesmo registro
        assert user.config.cidade == "cabo"


# ─────────────────────────────────────────────
# atualizar_config
# ─────────────────────────────────────────────

class TestAtualizarConfig:
    def test_atualiza_cidade(self, config_padrao):
        get_user(123, config_padrao)
        atualizar_config(123, cidade="recife")
        user = get_user(123, config_padrao)
        assert user.config.cidade == "recife"

    def test_atualiza_termos(self, config_padrao):
        get_user(123, config_padrao)
        novos_termos = ["machine learning", "ia"]
        atualizar_config(123, termos=novos_termos)
        user = get_user(123, config_padrao)
        assert user.config.termos == novos_termos

    def test_campo_desconhecido_nao_quebra(self, config_padrao, caplog):
        get_user(123, config_padrao)
        atualizar_config(123, campo_inexistente="valor")
        assert "campo_inexistente" in caplog.text


# ─────────────────────────────────────────────
# Operações de editais
# ─────────────────────────────────────────────

class TestOperacoesEditais:
    def test_adicionar_aceito(self, config_padrao, edital_ti):
        get_user(123, config_padrao)
        adicionar_aceito(123, edital_ti)
        user = get_user(123, config_padrao)
        assert len(user.aceitos) == 1
        assert user.aceitos[0].link == edital_ti.link

    def test_adicionar_rejeitado(self, config_padrao, edital_rejeitado):
        get_user(123, config_padrao)
        adicionar_rejeitado(123, edital_rejeitado)
        user = get_user(123, config_padrao)
        assert len(user.rejeitados) == 1

    def test_promover_rejeitado(self, config_padrao, edital_rejeitado):
        get_user(123, config_padrao)
        adicionar_rejeitado(123, edital_rejeitado)

        edital_rejeitado.aceito_em = datetime.now().isoformat()
        edital_rejeitado.encontrado_em = "reanalise_titulo"
        promover_rejeitado(123, edital_rejeitado)

        user = get_user(123, config_padrao)
        assert len(user.rejeitados) == 0
        assert len(user.aceitos) == 1
        assert user.aceitos[0].motivo is None

    def test_links_conhecidos(self, config_padrao, edital_ti, edital_rejeitado):
        user = get_user(123, config_padrao)
        user.aceitos.append(edital_ti)
        user.rejeitados.append(edital_rejeitado)
        salvar_user(123, user)

        user = get_user(123, config_padrao)
        links = user.links_conhecidos()
        assert edital_ti.link in links
        assert edital_rejeitado.link in links


# ─────────────────────────────────────────────
# Disponibilidade
# ─────────────────────────────────────────────

class TestDisponibilidade:
    def test_muda_de_offline_para_online(self, config_padrao):
        user = get_user(123, config_padrao)
        user.site_online = False
        user.site_offline_desde = datetime.now().isoformat()
        salvar_user(123, user)

        mudou = registrar_disponibilidade(123, online=True)
        assert mudou is True

        user = get_user(123, config_padrao)
        assert user.site_online is True
        assert user.site_offline_desde is None

    def test_muda_de_online_para_offline(self, config_padrao):
        user = get_user(123, config_padrao)
        user.site_online = True
        salvar_user(123, user)

        mudou = registrar_disponibilidade(123, online=False)
        assert mudou is True

        user = get_user(123, config_padrao)
        assert user.site_online is False
        assert user.site_offline_desde is not None

    def test_sem_mudanca_retorna_false(self, config_padrao):
        user = get_user(123, config_padrao)
        user.site_online = True
        salvar_user(123, user)

        mudou = registrar_disponibilidade(123, online=True)
        assert mudou is False

    def test_historico_registrado(self, config_padrao):
        user = get_user(123, config_padrao)
        user.site_online = True
        salvar_user(123, user)

        registrar_disponibilidade(123, online=False)
        user = get_user(123, config_padrao)
        assert len(user.historico_disponibilidade) == 1
        assert user.historico_disponibilidade[0].evento == "caiu"

    def test_tempo_offline_sem_registro(self, config_padrao):
        user = get_user(123, config_padrao)
        assert tempo_offline(user) == "desconhecido"