import os

# --- Strategy V3: Multi-Signal Score System ---

# Entry range (wider — score IS the filter, not the range alone)
ENTRY_NO_MIN = float(os.environ.get("ENTRY_NO_MIN", 0.78))
ENTRY_NO_MAX = float(os.environ.get("ENTRY_NO_MAX", 0.93))

# Score thresholds
MIN_ENTRY_SCORE = int(os.environ.get("MIN_ENTRY_SCORE", 60))

# Trailing stop
TRAIL_STOP_DISTANCE = float(os.environ.get("TRAIL_STOP_DISTANCE", 0.03))  # 3¢

# Exit rules
HALF_EXIT_GAIN   = float(os.environ.get("HALF_EXIT_GAIN", 0.07))   # vender 50% cuando +7¢
HARD_STOP_DROP   = float(os.environ.get("HARD_STOP_DROP", 0.05))   # hard stop si cae 5¢

# Volume thresholds for scoring
SCORE_VOLUME_HIGH = float(os.environ.get("SCORE_VOLUME_HIGH", 500))   # +20 pts
SCORE_VOLUME_MID  = float(os.environ.get("SCORE_VOLUME_MID", 300))    # +15 pts
SCORE_VOLUME_LOW  = float(os.environ.get("SCORE_VOLUME_LOW", 200))    # +10 pts

# Position sizing by score (linear interpolation)
BASE_POSITION_PCT = float(os.environ.get("BASE_POSITION_PCT", 0.06))   # 6% en score mínimo
MAX_POSITION_PCT  = float(os.environ.get("MAX_POSITION_PCT", 0.10))    # 10% en score 100

# Price history
PRICE_HISTORY_TTL = int(os.environ.get("PRICE_HISTORY_TTL", 3600))

# Shared scan parameters
MIN_VOLUME         = float(os.environ.get("MIN_VOLUME", 200))
MONITOR_INTERVAL   = int(os.environ.get("MONITOR_INTERVAL", 30))
SCAN_DAYS_AHEAD    = int(os.environ.get("SCAN_DAYS_AHEAD", 1))
MIN_LOCAL_HOUR     = int(os.environ.get("MIN_LOCAL_HOUR", 11))
MAX_POSITIONS      = int(os.environ.get("MAX_POSITIONS", 20))
PRICE_UPDATE_INTERVAL = int(os.environ.get("PRICE_UPDATE_INTERVAL", 10))

# Capital
INITIAL_CAPITAL = float(os.environ.get("INITIAL_CAPITAL", 100.0))
AUTO_MODE       = os.environ.get("AUTO_MODE", "true").lower() == "true"
AUTO_START      = os.environ.get("AUTO_START", "true").lower() == "true"

# API
GAMMA = os.environ.get("GAMMA_API", "https://gamma-api.polymarket.com")

# City UTC offsets — hardcoded (no tzdata on Railway slim Docker)
CITY_UTC_OFFSET = {
    "chicago":      -6,
    "dallas":       -6,
    "atlanta":      -5,
    "miami":        -5,
    "nyc":          -5,
    "boston":       -5,
    "toronto":      -5,
    "seattle":      -8,
    "los-angeles":  -8,
    "houston":      -6,
    "phoenix":      -7,
    "denver":       -7,
    "london":        0,
    "paris":         1,
    "ankara":        3,
    "seoul":         9,
    "wellington":   13,
    "sao-paulo":    -3,
    "buenos-aires": -3,
}

WEATHER_CITIES = [
    "chicago", "dallas", "atlanta", "miami", "nyc",
    "seattle", "london", "wellington", "toronto", "seoul",
    "ankara", "paris", "sao-paulo", "buenos-aires",
    "los-angeles", "houston", "phoenix", "denver", "boston",
]
