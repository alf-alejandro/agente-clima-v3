"""portfolio.py — V3 portfolio con trailing stop y salida parcial 50%.

Lógica de salida por posición:
  - Al abrir: trail_stop = entry_no - TRAIL_STOP_DISTANCE (3¢ abajo)
  - En cada update: trail_stop sube con el precio (nunca baja)
  - Salida parcial (50%): cuando current_no >= entry_no + HALF_EXIT_GAIN (7¢)
  - Cierre por trail: cuando current_no <= trail_stop
  - Hard stop: cuando current_no <= entry_no - HARD_STOP_DROP (5¢)

Statuses de cierre:
  WON        — NO resolvió ≥0.99
  LOST       — YES resolvió ≥0.99
  TRAIL_STOP — trailing stop activado
  HARD_STOP  — caída brusca desde entrada
  PARTIAL    — salida 50% registrada (posición sigue abierta con 50% restante)
  LIQUIDATED — auto-liquidación por rango
"""

import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from app.scanner import now_utc, fetch_market_live, get_prices
from app.config import (
    MAX_POSITIONS,
    TRAIL_STOP_DISTANCE,
    HALF_EXIT_GAIN,
    HARD_STOP_DROP,
)
import app.db as db

log = logging.getLogger(__name__)


class AutoPortfolio:
    def __init__(self, initial_capital):
        self.lock = threading.Lock()
        self.capital_inicial = initial_capital
        self.capital_total = initial_capital
        self.capital_disponible = initial_capital
        self.positions = {}
        self.closed_positions = []
        self.session_start = now_utc()
        self.capital_history = [
            {"time": now_utc().isoformat(), "capital": initial_capital}
        ]
        self._cap_record_count = 0

    def can_open_position(self):
        return (len(self.positions) < MAX_POSITIONS and
                self.capital_disponible >= 1)

    def open_position(self, opp, amount, score=0):
        tokens = amount / opp["no_price"]
        max_gain = tokens * 1.0 - amount
        entry_no = opp["no_price"]

        pos = {
            **opp,
            "entry_time":    now_utc().isoformat(),
            "entry_no":      entry_no,
            "current_no":    entry_no,
            "allocated":     amount,
            "tokens":        tokens,
            "max_gain":      max_gain,
            "trail_stop":    round(entry_no - TRAIL_STOP_DISTANCE, 4),
            "partial_done":  False,   # True después de la salida 50%
            "score":         score,
            "status":        "OPEN",
            "pnl":           0.0,
        }
        cid = opp["condition_id"]
        self.positions[cid] = pos
        self.capital_disponible -= amount
        db.upsert_open_position(cid, pos)
        db.save_state(self.capital_inicial, self.capital_total,
                      self.capital_disponible, self.session_start)
        return True

    def get_position_slugs(self):
        return [
            (cid, pos["slug"], pos.get("no_token_id"))
            for cid, pos in self.positions.items()
        ]

    def apply_price_updates(self, price_map):
        """Aplicar precios y gestionar resoluciones.
        Llamar con self.lock held."""
        to_close = []

        for cid, (yes_price, no_price) in price_map.items():
            if cid not in self.positions:
                continue
            pos = self.positions[cid]
            pos["current_no"] = no_price

            # Actualizar trailing stop (solo sube, nunca baja)
            new_trail = round(no_price - TRAIL_STOP_DISTANCE, 4)
            if new_trail > pos["trail_stop"]:
                pos["trail_stop"] = new_trail

            # Resolución YES (perdimos)
            if yes_price >= 0.99:
                resolution = (
                    f"YES resolvió — temperatura superó el umbral "
                    f"(YES={yes_price*100:.1f}¢)"
                )
                to_close.append((cid, "LOST", -pos["allocated"], resolution))
                continue

            # Resolución NO (ganamos)
            if no_price >= 0.99:
                resolution = (
                    f"NO resolvió — temperatura no superó el umbral "
                    f"(NO={no_price*100:.1f}¢)"
                )
                to_close.append((cid, "WON", pos["max_gain"], resolution))
                continue

            # Hard stop: caída > HARD_STOP_DROP desde entrada
            drop = no_price - pos["entry_no"]
            if drop <= -HARD_STOP_DROP:
                sale_value = pos["tokens"] * no_price
                realized = sale_value - pos["allocated"]
                resolution = (
                    f"Hard stop @ NO={no_price*100:.1f}¢ "
                    f"(entrada {pos['entry_no']*100:.1f}¢, caída {-drop*100:.1f}¢)"
                )
                to_close.append((cid, "HARD_STOP", realized, resolution))

        for cid, status, pnl, resolution in to_close:
            self._close_position(cid, status, pnl, resolution)

    def check_trail_exits(self):
        """Evaluar salida parcial 50% y activación de trailing stop.
        Llamar con self.lock held."""
        for cid, pos in list(self.positions.items()):
            current_no = pos["current_no"]
            entry_no   = pos["entry_no"]

            # Salida parcial 50%: cuando subimos HALF_EXIT_GAIN desde entrada
            if not pos["partial_done"] and current_no >= entry_no + HALF_EXIT_GAIN:
                self._partial_exit(cid)
                continue  # re-evaluar trail en el próximo ciclo

            # Trail stop activado (solo después de partial o si no hay partial aún)
            if current_no <= pos["trail_stop"]:
                sale_value = pos["tokens"] * current_no
                realized   = sale_value - pos["allocated"]
                resolution = (
                    f"Trail stop @ NO={current_no*100:.1f}¢ "
                    f"(trail={pos['trail_stop']*100:.1f}¢, "
                    f"entrada={entry_no*100:.1f}¢)"
                )
                self._close_position(cid, "TRAIL_STOP", realized, resolution)

    def _partial_exit(self, cid):
        """Vender 50% de los tokens al precio actual. Posición sigue abierta."""
        pos = self.positions[cid]
        fraction      = 0.50
        tokens_sold   = pos["tokens"] * fraction
        sale_value    = tokens_sold * pos["current_no"]
        cost_fraction = pos["allocated"] * fraction
        realized_pnl  = sale_value - cost_fraction

        pos["tokens"]       *= (1 - fraction)
        pos["allocated"]    *= (1 - fraction)
        pos["max_gain"]     *= (1 - fraction)
        pos["partial_done"]  = True

        self.capital_disponible += cost_fraction + realized_pnl
        self.capital_total      += realized_pnl

        partial_record = {
            "question":    pos["question"],
            "city":        pos.get("city", ""),
            "condition_id": cid,
            "entry_no":    pos["entry_no"],
            "allocated":   round(cost_fraction, 2),
            "pnl":         round(realized_pnl, 2),
            "score":       pos.get("score", 0),
            "status":      "PARTIAL",
            "resolution":  (
                f"Salida parcial 50% @ NO={pos['current_no']*100:.1f}¢ "
                f"(entrada+{HALF_EXIT_GAIN*100:.0f}¢)"
            ),
            "entry_time":  pos["entry_time"],
            "close_time":  now_utc().isoformat(),
        }
        self.closed_positions.append(partial_record)
        db.insert_closed_position(partial_record)
        db.upsert_open_position(cid, pos)  # actualizar posición reducida
        db.save_state(self.capital_inicial, self.capital_total,
                      self.capital_disponible, self.session_start)

    def _close_position(self, cid, status, pnl, resolution=""):
        if cid not in self.positions:
            return
        pos = self.positions[cid]
        pos["status"]     = status
        pos["pnl"]        = pnl
        pos["close_time"] = now_utc().isoformat()
        pos["resolution"] = resolution

        recovered = pos["allocated"] + pnl
        self.capital_disponible += recovered
        self.capital_total      += pnl

        closed_pos = pos.copy()
        self.closed_positions.append(closed_pos)
        del self.positions[cid]
        db.delete_open_position(cid)
        db.insert_closed_position(closed_pos)
        db.save_state(self.capital_inicial, self.capital_total,
                      self.capital_disponible, self.session_start)

    # ── Learning insights ─────────────────────────────────────────────────────

    def compute_insights(self):
        exclude = {"PARTIAL", "LIQUIDATED"}
        closed = [p for p in self.closed_positions if p["status"] not in exclude]
        if len(closed) < 5:
            return None

        by_hour = defaultdict(lambda: {"won": 0, "total": 0})
        by_city = defaultdict(lambda: {"won": 0, "total": 0})

        for pos in closed:
            try:
                hour = int(pos["entry_time"][11:13])
            except Exception:
                hour = -1
            city = pos.get("city", "unknown")
            won  = pos["pnl"] > 0

            if hour >= 0:
                by_hour[hour]["total"] += 1
                if won:
                    by_hour[hour]["won"] += 1

            by_city[city]["total"] += 1
            if won:
                by_city[city]["won"] += 1

        total     = len(closed)
        won_total = sum(1 for p in closed if p["pnl"] > 0)

        hour_stats = sorted(
            [{"hour": h, "win_rate": round(v["won"] / v["total"], 2), "trades": v["total"]}
             for h, v in by_hour.items() if v["total"] >= 2],
            key=lambda x: x["win_rate"], reverse=True,
        )
        city_stats = sorted(
            [{"city": c, "win_rate": round(v["won"] / v["total"], 2), "trades": v["total"]}
             for c, v in by_city.items() if v["total"] >= 2],
            key=lambda x: x["win_rate"], reverse=True,
        )

        return {
            "overall_win_rate": round(won_total / total, 2),
            "total_trades":     total,
            "by_hour":          hour_stats[:6],
            "by_city":          city_stats[:6],
        }

    # ── Capital snapshot ──────────────────────────────────────────────────────

    # ── State persistence ─────────────────────────────────────────────────────

    def save_state(self):
        db.save_state(self.capital_inicial, self.capital_total,
                      self.capital_disponible, self.session_start)

    def load_state(self):
        """Restaura estado desde DB al arrancar. Devuelve True si OK."""
        s = db.load_state()
        if not s:
            return False
        try:
            self.capital_inicial    = s["capital_inicial"]
            self.capital_total      = s["capital_total"]
            self.capital_disponible = s["capital_disponible"]
            self.positions          = db.load_open_positions()
            self.closed_positions   = db.load_closed_positions()
            hist = db.load_capital_history()
            if hist:
                self.capital_history = hist
            self.session_start = datetime.fromisoformat(s["session_start"])
            log.info(
                "Estado restaurado desde DB: capital=%.2f  abiertas=%d  cerradas=%d",
                self.capital_total, len(self.positions), len(self.closed_positions),
            )
            return True
        except Exception as e:
            log.warning("load_state error: %s", e)
            return False

    # ── Capital snapshot ──────────────────────────────────────────────────────

    def record_capital(self):
        ts = now_utc().isoformat()
        point = {"time": ts, "capital": round(self.capital_total, 2)}
        self.capital_history.append(point)
        if len(self.capital_history) > 500:
            self.capital_history = self.capital_history[-500:]
        self._cap_record_count += 1
        if self._cap_record_count % 120 == 0:  # cada ~1h (120 ciclos × 30s)
            db.append_capital_point(ts, round(self.capital_total, 2))

    def snapshot(self):
        pnl = self.capital_total - self.capital_inicial
        roi = (pnl / self.capital_inicial * 100) if self.capital_inicial else 0

        exclude = {"PARTIAL", "LIQUIDATED"}
        won        = sum(1 for p in self.closed_positions if p["pnl"] > 0  and p["status"] not in exclude)
        lost       = sum(1 for p in self.closed_positions if p["pnl"] <= 0 and p["status"] not in exclude)
        trail_stop = sum(1 for p in self.closed_positions if p["status"] == "TRAIL_STOP")
        hard_stop  = sum(1 for p in self.closed_positions if p["status"] == "HARD_STOP")
        partial    = sum(1 for p in self.closed_positions if p["status"] == "PARTIAL")
        liquidated = sum(1 for p in self.closed_positions if p["status"] == "LIQUIDATED")

        open_positions = []
        for pos in list(self.positions.values()):
            float_pnl = pos["tokens"] * pos["current_no"] - pos["allocated"]
            open_positions.append({
                "question":     pos["question"],
                "city":         pos.get("city", ""),
                "entry_no":     pos["entry_no"],
                "current_no":   pos["current_no"],
                "trail_stop":   pos["trail_stop"],
                "allocated":    round(pos["allocated"], 2),
                "pnl":          round(float_pnl, 2),
                "entry_time":   pos["entry_time"],
                "status":       pos["status"],
                "partial_done": pos.get("partial_done", False),
                "score":        pos.get("score", 0),
            })

        closed = []
        for pos in self.closed_positions:
            closed.append({
                "question":   pos["question"],
                "entry_no":   pos["entry_no"],
                "allocated":  round(pos["allocated"], 2),
                "pnl":        round(pos["pnl"], 2),
                "score":      pos.get("score", 0),
                "status":     pos["status"],
                "resolution": pos.get("resolution", ""),
                "entry_time": pos["entry_time"],
                "close_time": pos.get("close_time", ""),
            })

        return {
            "capital_inicial":    round(self.capital_inicial, 2),
            "capital_total":      round(self.capital_total, 2),
            "capital_disponible": round(self.capital_disponible, 2),
            "pnl":                round(pnl, 2),
            "roi":                round(roi, 2),
            "won":                won,
            "lost":               lost,
            "trail_stop":         trail_stop,
            "hard_stop":          hard_stop,
            "partial":            partial,
            "liquidated":         liquidated,
            "open_positions":     open_positions,
            "closed_positions":   closed,
            "capital_history":    self.capital_history,
            "session_start":      self.session_start.isoformat(),
            "insights":           self.compute_insights(),
        }
