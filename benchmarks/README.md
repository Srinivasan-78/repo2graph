<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​‌‌​‌​‌​​​‌​‌​‌​​‌​​‌​​‌‌​​‌‌​‌‌‌​​‌​​‌​‌​​​​​‌‌‌​‌‌‌​‌‌​​‌‌‌​‌​​​‌‌​​​‌‌​‌‌‌​‌‌​‌​​‌​‌​​​​‌​​‌‌‌​‌‌‌​‌‌‌​​‌‌​‌​‌​‌‌​​‌​‌​‌‌​​‌​‌‌​‌​​‌‌​​​​‌​‌​‌​​‌‌​‌​​‌‌​‌​‌​‌​​​​​‌​​‌​​​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.mEI3rPwgF7iBwsVVZaSMPH
-->
# Benchmarks

Machine-readable results from running `repo2graph build` against real, public repositories.

- **[`results.json`](results.json)** — one entry per repository, written by
  `scripts/generate_examples.py`: commit, `repo2graph` version, file/node/edge counts, clone and
  build wall-clock time, generation timestamp. Regenerated (merged by repository URL, not replaced
  wholesale) every time the generator runs — see [`methodology.md`](methodology.md).
- **[`methodology.md`](methodology.md)** — what is and is not controlled for, and how to reproduce
  a number in `results.json` yourself.

The narrative version of this data — tables, per-repository breakdowns, what the numbers actually
mean — is [docs/benchmarks.md](../docs/benchmarks.md). This directory is the machine-readable
source those tables are generated from; nothing in `docs/benchmarks.md` is hand-typed independently
of `results.json`.

## Reproduce

```bash
pip install -e "." pyyaml
python scripts/generate_examples.py --all --results benchmarks/results.json
```

See [examples/README.md](../examples/README.md) and
[docs/examples.md](../docs/examples.md) for what else this produces (the `examples/<id>/`
directories) and why the same run drives both.
