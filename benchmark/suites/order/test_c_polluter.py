import shared


def test_polluter_adds_item():
    shared.ITEMS.append("x")
    assert shared.ITEMS
