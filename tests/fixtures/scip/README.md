# SCIP fixture (#380)

`repo/` is a small Python package. `index.scip` is the index `scip-python` wrote for it, unedited.
`tests/test_scip.py` builds `repo/` with and without `--scip index.scip`.

The package is shaped so that name-only resolution fails:

- `geometry/shapes.py` has two classes that both define `area`, and `geometry/report.py` calls
  `c.area()` / `s.area()` on typed receivers. By name alone those calls are ambiguous (two
  candidates at 0.5); SCIP knows each receiver's type.
- `Circle` and `Square` derive from `modern.Shape`, but `legacy.Shape` comes first in discovery
  order, so by name alone both `INHERITS` edges point at `legacy.Shape` at confidence 1.0.
- `scripts/uncovered.py` is outside the index (`pyrightconfig.json` includes only `geometry/`),
  so its ambiguous call has to stay heuristic.

## Regenerating

Tool: `@sourcegraph/scip-python` 0.6.6 from npm, on Node 22.22.0. `env.json` holds `[]`, so
scip-python does not query pip for installed packages, and the external symbols are only the
Python stdlib's.

```bash
npm install @sourcegraph/scip-python@0.6.6
echo '[]' > env.json
npx scip-python index \
  --cwd tests/fixtures/scip/repo \
  --project-name geometry \
  --project-version 0.1.0 \
  --environment "$PWD/env.json" \
  --output "$PWD/tests/fixtures/scip/index.scip"
```

`pyrightconfig.json` in `repo/` matters. It makes `repo/` the project root, so document paths are
`geometry/shapes.py` rather than paths relative to an enclosing `pyproject.toml`. Without it,
pyright adopts the repository's own `pyproject.toml` as the root. Do not use `--target-only`
either: it makes document paths relative to the target directory.

If you edit anything under `repo/`, regenerate the index. The tests pin line numbers.
