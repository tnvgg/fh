import shared


def test_victim_needs_clean_state():
    assert shared.ITEMS == []
