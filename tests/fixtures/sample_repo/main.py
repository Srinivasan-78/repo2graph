"""Sample repository fixture for benchmark testing."""

from .service import process_data


def main():
    """Main entrypoint for sample application."""
    return process_data("input")
