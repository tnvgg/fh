def test_first_item_is_not_alpha():
    items = {"alpha", "bravo", "charlie", "delta"}
    assert next(iter(items)) != "alpha", "depends on hash order"
