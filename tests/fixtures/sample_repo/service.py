"""Service layer for sample application."""

from .utils import helper


def process_data(val: str) -> str:
    """Process incoming data string."""
    return helper(val)
