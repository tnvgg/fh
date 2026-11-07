import random


def test_roll_is_lucky():
    assert random.random() > 0.3, "unlucky roll"
