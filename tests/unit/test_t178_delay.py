from scripts.lab_t178_delay import delay_stats, histogram, nearest_signal, pvalue


def test_histogram_and_delay_stats():
    assert histogram([-61, -60, -1, 0, 5, 6, 61, None])["outside"] == 3
    stats = delay_stats([-2, 4, 35, None])
    assert stats["within_30"] == 2
    assert stats["positive"] == 1
    assert stats["positive_share"] == 0.5


def test_nearest_signal_is_owner_minus_signal():
    signals = [{"index": 10, "direction": "long"}, {"index": 20, "direction": "short"}]
    assert nearest_signal(13, "long", signals) == 3
    assert nearest_signal(None, "long", signals) is None


def test_lower_tail_pvalue_uses_plus_one():
    assert pvalue([1.0, 2.0, 3.0, 4.0], 2.0, lower=True) == 3 / 5
