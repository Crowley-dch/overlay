import time

from overlay.tunnel.relay_profile import (
    EMA_ALPHA,
    MIN_SAMPLES_FOR_RATING,
    RelayProfile,
    RelayProfileStore,
)


def _nid(i: int) -> bytes:
    return bytes([i]) * 32


def test_new_profile_default_score_is_half():
    p = RelayProfile(node_id=_nid(1))
    assert p.score() == 0.5
    assert p.samples == 0


def test_success_increases_ema():
    p = RelayProfile(node_id=_nid(1))
    initial = p.ema_success
    p.record_success(0.05)
    assert p.ema_success > initial
    assert p.successes == 1
    assert p.samples == 1


def test_failure_decreases_ema():
    p = RelayProfile(node_id=_nid(1))
    p.record_success(0.05)
    p.record_success(0.05)
    before = p.ema_success
    p.record_failure()
    assert p.ema_success < before
    assert p.failures == 1
    assert p.consecutive_failures == 1


def test_success_resets_consecutive_failures():
    p = RelayProfile(node_id=_nid(1))
    p.record_failure()
    p.record_failure()
    assert p.consecutive_failures == 2
    p.record_success(0.05)
    assert p.consecutive_failures == 0


def test_score_zero_after_two_consecutive_failures():
    p = RelayProfile(node_id=_nid(1))
    for _ in range(MIN_SAMPLES_FOR_RATING):
        p.record_success(0.05)
    p.record_failure()
    p.record_failure()
    assert p.score() == 0.0


def test_score_after_successes_is_high():
    p = RelayProfile(node_id=_nid(1))
    for _ in range(5):
        p.record_success(0.05)
    assert p.score() > 0.7


def test_score_below_default_until_samples_reached():
    p = RelayProfile(node_id=_nid(1))
    p.record_failure()
    p.record_failure()
    assert p.consecutive_failures == 2
    assert p.score() == 0.5
    assert p.samples < MIN_SAMPLES_FOR_RATING


def test_store_returns_profile():
    store = RelayProfileStore()
    p1 = store.get(_nid(1))
    p2 = store.get(_nid(1))
    assert p1 is p2


def test_store_records_and_scores():
    store = RelayProfileStore()
    for _ in range(4):
        store.record_success(_nid(1), 0.05)
    store.record_failure(_nid(2))
    store.record_failure(_nid(2))
    assert store.score(_nid(1)) > store.score(_nid(2))


def test_snapshot_serializable():
    store = RelayProfileStore()
    store.record_success(_nid(1), 0.05)
    snap = store.snapshot()
    key = _nid(1).hex()
    assert key in snap
    assert "score" in snap[key]
    assert "ema_success" in snap[key]