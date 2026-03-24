"""
server.py
Servidor Flask minimalista para health check.
Usado pelo Render/Railway para verificar se o processo está vivo.
Roda em thread separada, paralelo ao bot.
"""

from __future__ import annotations

import logging

from flask import Flask, jsonify

from bot.database import _carregar_raw

logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def home():
    return jsonify({"status": "ok", "bot": "SENAI Editais"})


@app.route("/health")
def health():
    """Retorna métricas agregadas de todos os usuários."""
    try:
        raw = _carregar_raw()
        users = raw.get("users", {})

        total_aceitos    = sum(len(u.get("aceitos", []))    for u in users.values())
        total_rejeitados = sum(len(u.get("rejeitados", [])) for u in users.values())
        total_buscas     = sum(u.get("stats", {}).get("total_buscas", 0) for u in users.values())
        total_pdfs       = sum(u.get("stats", {}).get("total_pdfs_baixados", 0) for u in users.values())

        return jsonify({
            "status":           "ok",
            "total_usuarios":   len(users),
            "total_aceitos":    total_aceitos,
            "total_rejeitados": total_rejeitados,
            "total_buscas":     total_buscas,
            "total_pdfs":       total_pdfs,
        })
    except Exception as exc:
        logger.error("Erro no health check: %s", exc)
        return jsonify({"status": "error", "detail": str(exc)}), 500


def iniciar(porta: int) -> None:
    """Inicia o servidor Flask. Chamado em thread daemon pelo main.py."""
    logger.info("Flask rodando na porta %d", porta)
    app.run(host="0.0.0.0", port=porta, use_reloader=False)