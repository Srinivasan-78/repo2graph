"""Two shapes that both define `area`, so a call by name alone is ambiguous."""

import math

from . import legacy, modern


class Circle(modern.Shape):
    def __init__(self, radius: float) -> None:
        super().__init__("circle")
        self.radius = radius

    def area(self) -> float:
        return math.pi * self.radius**2


class Square(modern.Shape):
    def __init__(self, side: float) -> None:
        super().__init__("square")
        self.side = side

    def area(self) -> float:
        return self.side * self.side

    @staticmethod
    def unit() -> "Square":
        return Square(1.0)


def retired() -> legacy.Shape:
    return legacy.Shape()
