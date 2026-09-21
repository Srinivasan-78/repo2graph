.PHONY: install lint format format-check typecheck test test-cov clean

install:
	pip install -e ".[dev,rag,mcp]"

lint:
	ruff check .

format:
	ruff format .

format-check:
	ruff format --check .

# Checks the whole package, not a hand-listed subset. pyproject's
# [[tool.mypy.overrides]] already relaxes exactly the legacy modules, so passing
# the package is equivalent to the old list *and* picks up a new module
# automatically -- the list form silently skipped anything not named in it.
typecheck:
	python -m mypy repo2graph/

test:
	pytest -q

test-cov:
	pytest --cov=repo2graph --cov-report=term-missing

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
