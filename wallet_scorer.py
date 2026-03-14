"""
Wallet Scorer
-------------
Scores a wallet profile 0-100.

Hard disqualifiers (score = 0):
  - Bot detected
  - Wallet age < 14 days
  - Fewer than 5 unique tokens traded
  - Net P&L <= 0 ETH
  - Data error

A wallet qualifies via ONE of two paths:
  Path A — Consistent winner: win rate >= 70%
  Path B — High conviction:   ROI >= 50% AND total P&L >= 5 ETH
            (catches wallets that lose small often but win very large)

Scoring weights:
  Win rate        30%
  ROI             30%
  P&L quality     20%
  Trade diversity 10%
  Wallet age      10%
"""

import logging

log = logging.getLogger(__name__)


class WalletScorer:

    # Hard disqualifiers
    MIN_AGE_DAYS      = 14
    MIN_UNIQUE_TOKENS = 5
    MIN_TOTAL_PNL     = 0.0

    # Qualification paths
    PATH_A_WIN_RATE   = 70.0   # Consistent winner
    PATH_B_MIN_ROI    = 50.0   # High conviction — ROI %
    PATH_B_MIN_PNL    = 5.0    # High conviction — minimum ETH profit
    PATH_C_MIN_PNL    = 50.0   # Absolute profit — regardless of win rate or ROI

    WEIGHTS = {
        "win_rate":        30,
        "roi":             30,
        "pnl_quality":     20,
        "trade_diversity": 10,
        "wallet_age":      10,
    }

    def score(self, profile: dict) -> dict:
        # Hard disqualifiers first
        reason = self._disqualify(profile)
        if reason:
            return {
                "total":             0,
                "breakdown":         {},
                "verdict":           "DISQUALIFIED",
                "disqualified":      True,
                "disqualify_reason": reason,
                "path":              None,
            }

        # Must qualify via at least one path
        win_rate   = profile.get("win_rate", 0)
        roi        = profile.get("roi_pct", 0)
        total_pnl  = profile.get("total_pnl_eth", 0)

        path_a = win_rate >= self.PATH_A_WIN_RATE
        path_b = roi >= self.PATH_B_MIN_ROI and total_pnl >= self.PATH_B_MIN_PNL
        path_c = total_pnl >= self.PATH_C_MIN_PNL

        if not path_a and not path_b and not path_c:
            return {
                "total":             0,
                "breakdown":         {},
                "verdict":           "DISQUALIFIED",
                "disqualified":      True,
                "disqualify_reason": (
                    f"Win rate {win_rate}% | ROI {roi:.1f}% | P&L {total_pnl:.2f} ETH — "
                    f"no qualification path met"
                ),
                "path": None,
            }

        path = (
            "A-consistent"     if path_a else
            "B-high-conviction" if path_b else
            "C-absolute-pnl"
        )

        breakdown = {
            "win_rate":        self._score_win_rate(win_rate),
            "roi":             self._score_roi(roi),
            "pnl_quality":     self._score_pnl(profile),
            "trade_diversity": self._score_diversity(profile.get("unique_tokens", 0)),
            "wallet_age":      self._score_age(profile.get("age_days", 0)),
        }

        total   = round(sum(breakdown[k] * (self.WEIGHTS[k] / 100) for k in breakdown))
        verdict = self._verdict(total)

        return {
            "total":             total,
            "breakdown":         breakdown,
            "verdict":           verdict,
            "disqualified":      False,
            "disqualify_reason": None,
            "path":              path,
        }

    def _disqualify(self, profile: dict) -> str | None:
        if profile.get("is_bot"):
            return "Bot detected"
        if profile.get("age_days", 0) < self.MIN_AGE_DAYS:
            return f"Wallet too new ({profile.get('age_days', 0)}d)"
        if profile.get("unique_tokens", 0) < self.MIN_UNIQUE_TOKENS:
            return f"Only {profile.get('unique_tokens', 0)} tokens traded"
        if profile.get("total_pnl_eth", 0) <= self.MIN_TOTAL_PNL:
            return f"Net P&L {profile.get('total_pnl_eth', 0):.4f} ETH — not profitable"
        if profile.get("error"):
            return "Data error"
        return None

    def _score_win_rate(self, win_rate: float) -> float:
        if win_rate < 10:   return 0
        if win_rate < 30:   return 20
        if win_rate < 50:   return 40
        if win_rate < 70:   return 60
        if win_rate < 80:   return 75
        if win_rate < 90:   return 88
        return 100

    def _score_roi(self, roi: float) -> float:
        if roi <= 0:     return 0
        if roi < 25:     return 20
        if roi < 50:     return 40
        if roi < 100:    return 60
        if roi < 200:    return 75
        if roi < 500:    return 88
        return 100

    def _score_pnl(self, profile: dict) -> float:
        total = profile.get("total_pnl_eth", 0)
        avg   = profile.get("avg_pnl_per_trade", 0)
        if total <= 0:   return 0
        if total < 1:    score = 20
        elif total < 5:  score = 40
        elif total < 20: score = 60
        elif total < 50: score = 75
        elif total < 100:score = 88
        else:            score = 100
        if avg > 0.5:    score = min(100, score + 10)
        return score

    def _score_diversity(self, unique_tokens: int) -> float:
        if unique_tokens < 5:   return 0
        if unique_tokens < 10:  return 30
        if unique_tokens < 25:  return 55
        if unique_tokens < 50:  return 75
        if unique_tokens < 100: return 88
        return 100

    def _score_age(self, age_days: int) -> float:
        if age_days < 14:   return 0
        if age_days < 30:   return 30
        if age_days < 60:   return 50
        if age_days < 120:  return 70
        if age_days < 180:  return 85
        return 100

    def _verdict(self, total: int) -> str:
        if total >= 80: return "STRONG"
        if total >= 65: return "WATCHLIST"
        if total >= 50: return "WEAK"
        return "SKIP"
