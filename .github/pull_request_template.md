## Summary

<!-- What does this change, and why? -->

Closes #<!-- issue number, if any -->

## Verification checklist

- [ ] Tests added or updated for the behavior this touches
- [ ] `ruff check .` passes
- [ ] `pytest` passes locally
- [ ] `mypy` passes on any module this PR adds or touches under `repo2graph/` that's covered
      by `[[tool.mypy.overrides]]` in `pyproject.toml` (new modules are strict by default)
- [ ] Docs updated (`architecture.md`, `docs/`, or [the invariants](CONTRIBUTING.md#architecture--os-compatibility-invariants)) if this changes user-facing behavior
      or a non-obvious repo convention
- [ ] If this touches `query.py`, `chunks.py`, `graph.py`, or `walker.py`: read the relevant
      section of [[the invariants](CONTRIBUTING.md#architecture--os-compatibility-invariants)](CONTRIBUTING.md) — each has a documented footgun
