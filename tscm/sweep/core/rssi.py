"""RSSI conditioning.

Raw RSSI is close to useless for ranging: a stationary phone on a desk swings
15 dB peak-to-peak from multipath, body blocking and antenna diversity. Two
stages fix that:

    KalmanRssi   1-D constant-position Kalman filter, removes per-sample noise
    Ranger       compares a short recent window against a longer baseline to
                 answer the only question that matters while walking around:
                 "warmer or colder?"

The Ranger is the part that turns a scanner into a finder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class KalmanRssi:
    """Scalar Kalman filter tuned for BLE advertisement RSSI.

    `q` (process noise) is how fast we believe the true signal can move; `r`
    (measurement noise) is how noisy we believe each sample is. BLE adverts on
    three channels with antenna diversity sit around r=8..12 dB^2. Raising q
    tracks motion faster at the cost of jitter.
    """

    __slots__ = ("q", "r", "_x", "_p")

    def __init__(self, q: float = 0.12, r: float = 9.0) -> None:
        self.q = q
        self.r = r
        self._x: float | None = None
        self._p: float = 1.0

    @property
    def value(self) -> float | None:
        return self._x

    def update(self, measurement: float) -> float:
        if self._x is None:
            self._x = measurement
            self._p = self.r
            return self._x
        # Predict: constant position, so only the covariance grows.
        p_pred = self._p + self.q
        # Update.
        k = p_pred / (p_pred + self.r)
        self._x = self._x + k * (measurement - self._x)
        self._p = (1.0 - k) * p_pred
        return self._x

    def reset(self) -> None:
        self._x = None
        self._p = 1.0


class Heat(str, Enum):
    """Ranging verdict, in the language you actually use while searching."""

    HOT = "hot"            # much closer
    WARMER = "warmer"
    STEADY = "steady"
    COOLER = "cooler"
    COLD = "cold"          # much farther
    LOST = "lost"          # no samples in the window
    CALIBRATING = "calibrating"

    @property
    def arrow(self) -> str:
        return {
            Heat.HOT: "▲▲",
            Heat.WARMER: "▲",
            Heat.STEADY: "•",
            Heat.COOLER: "▼",
            Heat.COLD: "▼▼",
            Heat.LOST: "??",
            Heat.CALIBRATING: "..",
        }[self]


@dataclass
class RangeReading:
    heat: Heat
    current_dbm: float | None
    baseline_dbm: float | None
    delta_db: float
    distance_m: float | None
    distance_ratio: float          # <1 means closer than baseline
    samples_recent: int
    samples_total: int
    age_s: float
    note: str = ""


@dataclass
class Ranger:
    """Walk-around direction finder for a single target.

    Compares the mean of the last `recent_window` seconds against a rolling
    baseline of the last `baseline_window` seconds. Thresholds are in dB and
    deliberately wide — 3 dB is inside the noise floor of a handheld sweep, so
    anything under that reads as "steady" rather than flickering.
    """

    recent_window: float = 3.0
    baseline_window: float = 20.0
    warm_db: float = 3.0
    hot_db: float = 8.0
    lost_after: float = 6.0
    env_factor: float = 2.6

    _samples: list[tuple[float, float]] = field(default_factory=list, repr=False)
    # Much livelier than the archival filter: an operator walking at 1 m/s needs
    # the display to keep up with them, and a filter that closes ~10% of the gap
    # per advertisement lags several metres behind. q=1.0/r=6.0 closes ~33% per
    # sample, which settles in about ten packets — roughly one second of BLE.
    _filter: KalmanRssi = field(
        default_factory=lambda: KalmanRssi(q=1.0, r=6.0), repr=False
    )
    _total: int = 0

    def reset(self) -> None:
        self._samples.clear()
        self._filter = KalmanRssi(q=1.0, r=6.0)
        self._total = 0

    def feed(self, ts: float, rssi: float) -> None:
        self._filter.update(rssi)
        self._samples.append((ts, self._filter.value or rssi))
        self._total += 1
        cutoff = ts - max(self.baseline_window, self.recent_window) * 2
        if self._samples and self._samples[0][0] < cutoff:
            self._samples = [s for s in self._samples if s[0] >= cutoff]

    def _mean(self, now: float, window: float, *, exclude_recent: float = 0.0) -> tuple[float | None, int]:
        lo = now - window
        hi = now - exclude_recent
        vals = [v for ts, v in self._samples if lo <= ts <= hi]
        if not vals:
            return None, 0
        return sum(vals) / len(vals), len(vals)

    def read(self, now: float) -> RangeReading:
        recent, n_recent = self._mean(now, self.recent_window)
        baseline, _ = self._mean(now, self.baseline_window, exclude_recent=self.recent_window)
        age = now - self._samples[-1][0] if self._samples else float("inf")

        distance = None
        if recent is not None:
            distance = self._distance_from(recent)

        if not self._samples or age > self.lost_after:
            return RangeReading(
                Heat.LOST, recent, baseline, 0.0, distance, 1.0, n_recent, self._total,
                age if self._samples else float("inf"),
                note="no packets in window — move slowly, it may be shielded or asleep",
            )

        if baseline is None or self._total < 6:
            return RangeReading(
                Heat.CALIBRATING, recent, baseline, 0.0, distance, 1.0, n_recent,
                self._total, age,
                note="hold still ~10s to set a baseline, then start walking",
            )

        delta = (recent or 0.0) - baseline
        ratio = 10 ** (-delta / (10.0 * self.env_factor))

        if delta >= self.hot_db:
            heat = Heat.HOT
        elif delta >= self.warm_db:
            heat = Heat.WARMER
        elif delta <= -self.hot_db:
            heat = Heat.COLD
        elif delta <= -self.warm_db:
            heat = Heat.COOLER
        else:
            heat = Heat.STEADY

        return RangeReading(
            heat, recent, baseline, round(delta, 1), distance, round(ratio, 2),
            n_recent, self._total, round(age, 1),
        )

    def _distance_from(self, dbm: float, tx_power: float = -59.0) -> float | None:
        try:
            return round(10 ** ((tx_power - dbm) / (10.0 * self.env_factor)), 2)
        except (OverflowError, ValueError):
            return None


def path_loss_env_factor(label: str) -> float:
    """Named environments, because asking a user for `n` is a bad interface."""
    return {
        "open": 2.0,
        "room": 2.4,
        "office": 2.7,
        "home": 2.8,
        "cluttered": 3.2,
        "through-wall": 3.6,
    }.get(label.lower(), 2.6)
