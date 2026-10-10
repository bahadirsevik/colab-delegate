import time

from colab_delegate.config import LifecycleCfg
from colab_delegate.lifecycle.budget import can_wake, estimate_units
from colab_delegate.lifecycle.state import FileLock, RuntimeState, StateStore


def test_can_wake_rules():
    cfg = LifecycleCfg(min_units_to_wake=5, max_cold_starts_per_day=2, max_session_minutes=90)
    st = RuntimeState()
    assert can_wake(100, st, cfg, "A100").ok
    assert not can_wake(4.9, st, cfg, "A100").ok
    st.cold_starts = [time.time() - 10, time.time() - 20]
    assert "cold starts today 2" in can_wake(100, st, cfg, "A100").reason
    st.cold_starts = [time.time() - 90000]  # dün
    assert can_wake(100, st, cfg, "A100").ok
    assert not can_wake(1.5, RuntimeState(), LifecycleCfg(min_units_to_wake=1), "A100").ok  # 90 dk A100 ~8 birim, %25'i 2


def test_estimate_units_prefers_measured_rate():
    assert estimate_units("A100", 60) == 5.3
    assert estimate_units("A100", 30, measured_rate=5.38) == 2.69


def test_state_roundtrip_and_lock(tmp_path):
    store = StateStore(tmp_path / "state.json")
    with store.update() as st:
        st.stage = "ready"
        st.endpoint = "gpu-a100-x"
        st.cold_starts.append(time.time())
    st2 = store.load()
    assert st2.stage == "ready" and st2.endpoint == "gpu-a100-x" and st2.cold_starts_today() == 1
    lock = FileLock(tmp_path / "lock")
    with lock:
        assert not FileLock(tmp_path / "lock").acquire(timeout=0.0)
    assert FileLock(tmp_path / "lock").acquire(timeout=0.0)
