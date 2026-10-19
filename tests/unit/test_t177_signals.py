from scripts.lab_t177_signals import counts, nearest, pvalue


def test_counts_and_nearest():
    signals = [{"index": 10, "direction": "long"}, {"index": 20, "direction": "short"}]
    assert nearest(13, "long", signals) == -3
    assert counts([-3, 8, 40, None]) == {"3": 1, "10": 2, "30": 2}


def test_pvalue_is_one_sided_with_plus_one():
    assert pvalue([0, 1, 2, 2], 2) == 3 / 5

