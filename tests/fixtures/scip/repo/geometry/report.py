"""Calls through typed receivers: exact with SCIP, ambiguous by name alone."""

from typing import Callable

from .shapes import Circle, Square


def circle_area(c: Circle) -> float:
    return c.area()


def both_areas(c: Circle, s: Square) -> float:
    return c.area() + s.area()


def labels(c: Circle) -> str:
    return c.describe()


def apply(fn: Callable[[], float]) -> float:
    return fn()


def summary() -> str:
    c = Circle(2.0)
    s = Square.unit()
    total = both_areas(c, s) + circle_area(c)
    print(labels(c))
    return f"{total:.2f} over {len([c, s])} shapes"
