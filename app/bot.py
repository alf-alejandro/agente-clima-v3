"""bot.py — V3 main loop con sistema de puntuación multi-señal.

Ciclo:
  1. Gamma discovery → candidatos (NO 0.50–0.97, volumen OK)
  2. CLOB price para cada candidato → record en market_scorer
  3. score() → si score >= MIN_ENTRY_SCORE Y NO en 0.78–0.93 → entra
  4. amount = calc_position_size(capital_disponible, score)
  5. apply_price_updates → resoluciones + trail update
  6. check_trail_exits → partial 50% + trail stop + hard stop
  7. Auto-liquidar posiciones fuera de rango
  8. purge_old en scorer
"""

import threading
import logging
from datetime import datetime, timezone

from app.scanner import (
    scan_opportunities, fetch_live_prices, fetch_no_price_clob,
)
from app.config import (
    MONITOR_INTERVAL, PRICE_UPDATE_INTERVAL, MAX_POSITIONS,
    ENTRY_NO_MIN, ENTRY_NO_MAX, MIN_ENTRY_SCORE,
    BASE_POSITION_PCT, MAX_POSITION_PCT,
)

log = logging.getLogger(__name__)

MAX_CLOB_VERIFY = 20


def calc_position_size(capital_disponible: float, score: int) -> float:
    """Sizing proporcional al score (interpolación lineal 60→100 : 6%→10%).

    score 60  →  6% de capital_disponible
    score 100 → 10% de capital_disponible
    """
    score_range = 100 - MIN_ENTRY_SCORE  # 40
    if score_range <= 0:
        pct = BASE_POSITION_PCT
    else:
        t   = (score - MIN_ENTRY_SCORE) / score_range
        t   = max(0.0, min(1.0, t))
        pct = BASE_POSITION_PCT + t * (MAX_POSITION_PCT - BASE_POSITION_PCT)
    return min(capital_disponible * pct, capital_disponible)


class BotRunner:
    def __init__(self, portfolio, scorer):
        self.portfolio = portfolio
        self.scorer    = scorer
        self._stop_event  = threading.Event()
        self._thread      = None
        self._price_thread = None
        self.scan_count   = 0
        self.last_opportunities = []
        self.status           = "stopped"
        self.last_price_update = None

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ── Thread management ──────────────────────────────────────────────────────

    def start(self):
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread       = threading.Thread(target=self._run,        daemon=True)
        self._price_thread = threading.Thread(target=self._run_prices, daemon=True)
        self._thread.start()
        self._price_thread.start()
        self.status = "running"

    def stop(self):
        self._stop_event.set()
        self.status = "stopped"

    # ── Main scan loop ─────────────────────────────────────────────────────────

    def _run(self):
        log.info("Bot V3 iniciado — Multi-Signal Score System")
        while not self._stop_event.is_set():
            try:
                self._cycle()
            except Exception:
                log.exception("Error en ciclo V3")
            self._stop_event.wait(MONITOR_INTERVAL)
        log.info("Bot V3 detenido")

    def _cycle(self):
        self.scan_count += 1
        portfolio = self.portfolio
        scorer    = self.scorer

        # Watchdog: restart price thread si cayó
        if self._price_thread is not None and not self._price_thread.is_alive():
            log.warning("Price thread caído — reiniciando")
            self._price_thread = threading.Thread(target=self._run_prices, daemon=True)
            self._price_thread.start()

        # 1. IDs ya en portfolio (evitar re-entrada)
        with portfolio.lock:
            existing_ids = set(portfolio.positions.keys())
            closed_ids   = {
                p["condition_id"] for p in portfolio.closed_positions
                if p.get("condition_id")
            }
            existing_ids |= closed_ids

        # 2. Gamma discovery
        candidates = scan_opportunities(existing_ids)

        # 3. CLOB + score para cada candidato
        clob_verified = []   # [(opp, score_result)] — confirmados para entrada
        display_opps  = []
        clob_fails    = 0
        clob_ok       = True

        for opp in candidates[:MAX_CLOB_VERIFY]:
            if self._stop_event.is_set():
                return
            no_tid = opp.get("no_token_id")
            rt_yes, rt_no = None, None

            if clob_ok and no_tid:
                rt_yes, rt_no = fetch_no_price_clob(no_tid)
                # Sanity: descartar tokens invertidos (NO < 0.50)
                if rt_no is not None and rt_no < 0.50:
                    rt_yes, rt_no = None, None
                if rt_no is None:
                    clob_fails += 1
                    if clob_fails >= 2:
                        clob_ok = False

            if rt_no is None:
                display_opps.append({
                    **opp,
                    "score_total": 0, "score_zone": "-",
                    "score_traj": 0, "clob_ok": False,
                })
                continue

            # Registrar observación en scorer
            scorer.record(opp["condition_id"], rt_no, opp["volume"], opp["city"])

            opp = {**opp, "no_price": rt_no, "yes_price": rt_yes or round(1 - rt_no, 4)}
            score_result = scorer.score(opp["condition_id"], opp["city"])
            score_total  = score_result["total"]

            display_opps.append({
                **opp,
                "score_total": score_total,
                "score_zone":  score_result["zone"],
                "score_traj":  score_result["trajectory"],
                "score_obs":   score_result["observations"],
                "clob_ok":     True,
            })

            # Puerta de entrada: precio en rango Y score suficiente
            if ENTRY_NO_MIN <= rt_no <= ENTRY_NO_MAX and score_total >= MIN_ENTRY_SCORE:
                clob_verified.append((opp, score_result))
            elif ENTRY_NO_MIN <= rt_no <= ENTRY_NO_MAX:
                log.info(
                    "Score insuficiente %s — %.1f¢, score=%d (mínimo %d)",
                    opp["question"][:35], rt_no * 100, score_total, MIN_ENTRY_SCORE,
                )

        # Completar display con candidatos sin CLOB (para el dashboard)
        display_opps.extend(candidates[MAX_CLOB_VERIFY:MAX_CLOB_VERIFY + (20 - len(display_opps))])

        self.last_opportunities = [
            {
                "question":    o["question"],
                "no_price":    o["no_price"],
                "yes_price":   o["yes_price"],
                "volume":      o["volume"],
                "profit_cents": o["profit_cents"],
                "score_total": o.get("score_total", 0),
                "score_zone":  o.get("score_zone", "-"),
                "score_traj":  o.get("score_traj", 0),
                "score_obs":   o.get("score_obs", 0),
                "clob_ok":     o.get("clob_ok", False),
            }
            for o in display_opps[:20]
        ]

        # 4. Fetch precios posiciones abiertas — CLOB primero, Gamma fallback
        with portfolio.lock:
            pos_data = [
                (cid, pos.get("no_token_id"), pos.get("slug"))
                for cid, pos in portfolio.positions.items()
            ]

        price_map    = {}
        clob_ok_pos  = True
        clob_fail_pos = 0
        for cid, no_tid, slug in pos_data:
            if self._stop_event.is_set():
                return
            yes_p, no_p = None, None
            if clob_ok_pos:
                yes_p, no_p = fetch_no_price_clob(no_tid)
                if no_p is not None and no_p < 0.50:
                    yes_p, no_p = None, None
                if no_p is None:
                    clob_fail_pos += 1
                    if clob_fail_pos >= 2:
                        clob_ok_pos = False
            if no_p is None:
                yes_p, no_p = fetch_live_prices(slug)
            if yes_p is not None and no_p is not None:
                price_map[cid] = (yes_p, no_p)

        # 5. Operaciones de portfolio (con lock — sin HTTP)
        with portfolio.lock:
            # Abrir nuevas posiciones
            for opp, score_result in clob_verified:
                if not portfolio.can_open_position():
                    break
                score_total = score_result["total"]
                amount = calc_position_size(portfolio.capital_disponible, score_total)
                if amount >= 1:
                    portfolio.open_position(opp, amount, score=score_total)
                    log.info(
                        "Abierta: %s @ %.1f¢  $%.2f  score=%d (zona %s)",
                        opp["question"][:40], opp["no_price"] * 100,
                        amount, score_total, score_result["zone"],
                    )

            # Aplicar actualizaciones de precio (resoluciones + trail update)
            if price_map:
                portfolio.apply_price_updates(price_map)

            # Auto-liquidar posiciones con entrada fuera de rango
            for cid, pos in list(portfolio.positions.items()):
                entry_no = pos.get("entry_no", 1.0)
                if not (ENTRY_NO_MIN <= entry_no <= ENTRY_NO_MAX):
                    current_no = pos.get("current_no", entry_no)
                    pnl = round(pos["tokens"] * current_no - pos["allocated"], 2)
                    log.warning(
                        "Auto-liquidar %s — entrada %.1f¢ fuera de rango",
                        pos["question"][:40], entry_no * 100,
                    )
                    portfolio._close_position(
                        cid, "LIQUIDATED", pnl,
                        resolution=(
                            f"Auto-liquidación: entrada {entry_no*100:.1f}¢ "
                            f"fuera del rango ({ENTRY_NO_MIN*100:.0f}–{ENTRY_NO_MAX*100:.0f}¢)"
                        ),
                    )

            # Trail exits: partial 50% + trailing stop
            portfolio.check_trail_exits()
            portfolio.record_capital()

        # 6. Purge scorer histories viejos (fuera de lock)
        scorer.purge_old()

    # ── Price update loop ──────────────────────────────────────────────────────

    def _run_prices(self):
        log.info("Price updater V3 iniciado")
        while not self._stop_event.is_set():
            self._stop_event.wait(PRICE_UPDATE_INTERVAL)
            if self._stop_event.is_set():
                break
            try:
                self._refresh_prices()
            except Exception:
                log.exception("Error actualizando precios")
        log.info("Price updater V3 detenido")

    def _refresh_prices(self):
        """Fetch precios CLOB → Gamma fallback, con circuit breaker."""
        with self.portfolio.lock:
            pos_data = [
                (cid, pos.get("no_token_id"), pos.get("slug"))
                for cid, pos in self.portfolio.positions.items()
            ]

        clob_ok       = True
        clob_failures = 0

        for cid, no_tid, slug in pos_data:
            if self._stop_event.is_set():
                return

            yes_p, no_p = None, None
            source = "Gamma"

            if clob_ok and no_tid:
                yes_p, no_p = fetch_no_price_clob(no_tid)
                if no_p is not None:
                    if no_p < 0.50:
                        yes_p, no_p = None, None
                        clob_failures += 1
                    else:
                        source = "CLOB"
                        clob_failures = 0
                else:
                    clob_failures += 1
                if clob_failures >= 2:
                    clob_ok = False

            if no_p is None:
                yes_p, no_p = fetch_live_prices(slug)

            if no_p is None:
                continue

            with self.portfolio.lock:
                if cid in self.portfolio.positions:
                    pos = self.portfolio.positions[cid]
                    old = pos["current_no"]
                    pos["current_no"] = no_p
                    # Actualizar trail en tiempo real
                    from app.config import TRAIL_STOP_DISTANCE
                    new_trail = round(no_p - TRAIL_STOP_DISTANCE, 4)
                    if new_trail > pos["trail_stop"]:
                        pos["trail_stop"] = new_trail
                    if abs(no_p - old) >= 0.001:
                        log.info(
                            "Precio [%s] %s: %.4f → %.4f",
                            source, slug[:30] if slug else cid[:20], old, no_p,
                        )

        self.last_price_update = datetime.now(timezone.utc)
