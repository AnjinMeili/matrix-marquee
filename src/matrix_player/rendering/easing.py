"""Easing curves shared by reveals and transitions. Each maps 0..1 to 0..1."""


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def ease_out_cubic(x: float) -> float:
    """Fast start, gentle landing: for things arriving."""
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_in_cubic(x: float) -> float:
    """Gentle start, quick finish: for things leaving."""
    x = clamp01(x)
    return x ** 3


def ease_out_back(x: float, overshoot: float = 1.70158) -> float:
    """Arrives, overshoots a little, and settles: the guide's overshoot swing."""
    x = clamp01(x)
    return 1 + (overshoot + 1) * (x - 1) ** 3 + overshoot * (x - 1) ** 2
