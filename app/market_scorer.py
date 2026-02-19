"""market_scorer.py — Sistema de puntuación multi-señal para V3.

Cada mercado acumula un historial de (timestamp, no_price, volume).
El score 0-100 combina 4 señales independientes:

  Precio  (0/10/20/30): zona A=30, B=20, C=10, fuera=0
  Trayectoria (0/10/20/30): estable=30, alza gradual=20, alza rápida=10, caída=0
  Volumen (0/10/15/20): >500=20, 300-500=15, 200-300=10, <200=0
  Tiempo  (0/10/15/20): ≥16h local=20, 14-16h=15, 12-14h=10, <12h=0

Mínimo 60 pts para abrir posición.
"""

import threading
import logging
from datetime import datetime, timezone, timedelta

from app.config import (
    PRICE_HISTORY_TTL,
    SCORE_VOLUME_HIGH, SCORE_VOLUME_MID, SCORE_VOLUME_LOW,
    CITY_UTC_OFFSET,
)

log = logging.getLogger(__name__)

MAX_HISTORY_PER_MARKET = 50


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _now_utc():
    return datetime.now(timezone.utc)


class MarketScorer:
    def __init__(self):
        # {condition_id: [(timestamp, no_price, volume), ...]}
        self._history: dict[str, list[tuple[float, float, float]]] = {}
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def record(self, condition_id: str, no_price: float, volume: float, city: str):
        """Append one CLOB observation. Keeps last MAX_HISTORY_PER_MARKET entries."""
        ts = _now_ts()
        with self._lock:
            if condition_id not in self._history:
                self._history[condition_id] = []
            hist = self._history[condition_id]
            hist.append((ts, no_price, volume))
            if len(hist) > MAX_HISTORY_PER_MARKET:
                self._history[condition_id] = hist[-MAX_HISTORY_PER_MARKET:]

    def score(self, condition_id: str, city: str) -> dict:
        """Return full score breakdown for a market.

        Returns:
            {
              'total': int,      # 0-100
              'price': int,      # price zone sub-score
              'trajectory': int, # trajectory sub-score
              'volume': int,     # volume sub-score
              'time': int,       # time-of-day sub-score
              'observations': int,
              'zone': str,       # 'A' / 'B' / 'C' / '-'
            }
        """
        with self._lock:
            hist = list(self._history.get(condition_id, []))

        if not hist:
            return {"total": 0, "price": 0, "trajectory": 0,
                    "volume": 0, "time": 0, "observations": 0, "zone": "-"}

        last_ts, last_no, last_vol = hist[-1]

        price_pts, zone = self._price_score(last_no)
        traj_pts        = self._trajectory_score(hist)
        vol_pts         = self._volume_score(last_vol)
        time_pts        = self._time_score(city)

        total = price_pts + traj_pts + vol_pts + time_pts

        return {
            "total":        total,
            "price":        price_pts,
            "trajectory":   traj_pts,
            "volume":       vol_pts,
            "time":         time_pts,
            "observations": len(hist),
            "zone":         zone,
        }

    def get_all_scores(self) -> dict:
        """Return {condition_id: score_dict} for all tracked markets.
        Used by /api/scores endpoint.
        """
        result = {}
        with self._lock:
            cids = list(self._history.keys())
        for cid in cids:
            # score() acquires its own lock internally
            result[cid] = self.score(cid, "")  # city="" → time_pts=0 (no city context)
        return result

    def purge_old(self):
        """Remove histories not updated within PRICE_HISTORY_TTL seconds."""
        cutoff = _now_ts() - PRICE_HISTORY_TTL
        purged = 0
        with self._lock:
            to_delete = [
                cid for cid, hist in self._history.items()
                if hist and hist[-1][0] < cutoff
            ]
            for cid in to_delete:
                del self._history[cid]
                purged += 1
        if purged:
            log.info("MarketScorer purged %d stale histories", purged)

    # ── Sub-scores ─────────────────────────────────────────────────────────────

    def _price_score(self, no_price: float) -> tuple[int, str]:
        """Return (points, zone_label).

        Zona A (sweet spot): 0.85–0.91  → 30 pts
        Zona B (edges):      0.80–0.85 ó 0.91–0.94 → 20 pts
        Zona C (extremo):    0.78–0.80  → 10 pts
        Fuera de rango:      0 pts
        """
        if 0.85 <= no_price <= 0.91:
            return 30, "A"
        if (0.80 <= no_price < 0.85) or (0.91 < no_price <= 0.94):
            return 20, "B"
        if 0.78 <= no_price < 0.80:
            return 10, "C"
        return 0, "-"

    def _trajectory_score(self, hist: list) -> int:
        """Score based on last 4 observations.

        Estable     (variación < 1¢):       30 pts
        Alza gradual (0.5–2¢ por paso avg): 20 pts
        Alza rápida  (>2¢ por paso avg):    10 pts
        Caída / errática:                    0 pts

        Requiere al menos 4 observaciones.
        """
        if len(hist) < 4:
            return 0

        prices = [p for _, p, _ in hist[-4:]]
        variation = max(prices) - min(prices)
        avg_change = (prices[-1] - prices[0]) / (len(prices) - 1)

        # Alza rápida: >2¢ por paso (avg)
        if avg_change > 0.02:
            return 10
        # Alza gradual: 0.5–2¢ por paso (avg)
        if avg_change >= 0.005:
            return 20
        # Estable: variación total < 1¢ (incluye mercados flat o subiendo lentamente)
        if variation < 0.01:
            return 30
        # Caída o errática
        return 0

    def _volume_score(self, volume: float) -> int:
        if volume >= SCORE_VOLUME_HIGH:
            return 20
        if volume >= SCORE_VOLUME_MID:
            return 15
        if volume >= SCORE_VOLUME_LOW:
            return 10
        return 0

    def _time_score(self, city: str) -> int:
        """Score based on local hour for the city.

        ≥ 16h local → 20 pts  (clima casi decidido)
        14–16h local → 15 pts
        12–14h local → 10 pts
        < 12h        →  0 pts
        """
        offset = CITY_UTC_OFFSET.get(city)
        if offset is None:
            return 0
        local_now = _now_utc() + timedelta(hours=offset)
        h = local_now.hour
        if h >= 16:
            return 20
        if h >= 14:
            return 15
        if h >= 12:
            return 10
        return 0
