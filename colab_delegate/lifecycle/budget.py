"""Budget guards. Units are COMPUTE UNITS; the real cost driver is allocation minutes."""
from __future__ import annotations

from dataclasses import dataclass

from colab_delegate.config import LifecycleCfg
from colab_delegate.lifecycle.state import RuntimeState

# Faz 2 ölçümü (A100 40GB ≈ 5.2-5.4 birim/saat). Diğerleri kaba tahmin; `colab usage` oranı esastır.
APPROX_UNITS_PER_HOUR = {"A100": 5.3, "L4": 2.4, "T4": 1.8, "H100": 11.0, "G4": 3.0, "CPU": 0.08}


@dataclass
class WakeDecision:
    ok: bool
    reason: str = ""


def can_wake(balance: float, state: RuntimeState, cfg: LifecycleCfg, gpu: str) -> WakeDecision:
    """Decide whether the instance may be woken given balance and daily limits."""
    if balance < cfg.min_units_to_wake:
        return WakeDecision(False, f"balance {balance:.2f} < min_units_to_wake {cfg.min_units_to_wake}")
    n = state.cold_starts_today()
    if n >= cfg.max_cold_starts_per_day:
        return WakeDecision(False, f"cold starts today {n} >= max {cfg.max_cold_starts_per_day}")
    est = APPROX_UNITS_PER_HOUR.get(gpu, 5.0) * (cfg.max_session_minutes / 60.0)
    if balance < est * 0.25:
        return WakeDecision(False, f"balance {balance:.2f} too low for a {cfg.max_session_minutes} min session (~{est:.1f} units)")
    return WakeDecision(True, f"balance {balance:.2f}, cold starts today {n}")


def estimate_units(gpu: str, minutes: float, measured_rate: float | None = None) -> float:
    """Estimate compute units for a session of the given length on a GPU."""
    rate = measured_rate if measured_rate else APPROX_UNITS_PER_HOUR.get(gpu, 5.0)
    return round(rate * minutes / 60.0, 2)
