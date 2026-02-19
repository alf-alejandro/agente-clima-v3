from flask import Blueprint, render_template, jsonify

bp = Blueprint("main", __name__)

# Set by app factory
bot       = None
portfolio = None
scorer    = None


def init_routes(bot_instance, portfolio_instance, scorer_instance):
    global bot, portfolio, scorer
    bot       = bot_instance
    portfolio = portfolio_instance
    scorer    = scorer_instance


@bp.route("/")
def dashboard():
    return render_template("dashboard.html")


@bp.route("/api/status")
def api_status():
    with portfolio.lock:
        snap = portfolio.snapshot()
    snap["bot_status"]    = bot.status if bot else "unknown"
    snap["scan_count"]    = bot.scan_count if bot else 0
    snap["last_opportunities"] = bot.last_opportunities if bot else []
    lpu = bot.last_price_update if bot else None
    snap["last_price_update"]  = lpu.isoformat() if lpu else None
    snap["price_thread_alive"] = (
        bot._price_thread is not None and bot._price_thread.is_alive()
    ) if bot else False

    # Scorer summary
    if scorer:
        all_scores = scorer.get_all_scores()
        snap["tracked_markets"] = len(all_scores)
        top = sorted(all_scores.values(), key=lambda s: s["total"], reverse=True)
        snap["top_score"] = top[0]["total"] if top else 0
    else:
        snap["tracked_markets"] = 0
        snap["top_score"]       = 0

    return jsonify(snap)


@bp.route("/api/bot/start", methods=["POST"])
def api_bot_start():
    bot.start()
    return jsonify({"status": "running"})


@bp.route("/api/bot/stop", methods=["POST"])
def api_bot_stop():
    bot.stop()
    return jsonify({"status": "stopped"})


@bp.route("/api/scores")
def api_scores():
    """Devuelve scores de todos los mercados en seguimiento."""
    if not scorer:
        return jsonify({})
    all_scores = scorer.get_all_scores()
    # Ordenar por score desc para facilitar debug
    sorted_scores = dict(
        sorted(all_scores.items(), key=lambda kv: kv[1]["total"], reverse=True)
    )
    return jsonify(sorted_scores)
