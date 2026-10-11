"""Outside the SCIP index: scip-python ran with --target-only geometry."""

from geometry.shapes import Circle, Square


def measure(shape: Circle | Square) -> float:
    return shape.area()
