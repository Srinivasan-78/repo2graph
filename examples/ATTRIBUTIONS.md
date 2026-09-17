# Attributions

Every repository under `examples/` is someone else's copyrighted work. This file records what was
analyzed, under what license, what was and was not copied out of it, and what attribution each
license requires. License identifiers below were pulled from each repository's own license file
(`COPYING`/`LICENSE`) or GitHub's license API on 2026-09-17 — not assumed.

## Why `chunks.jsonl` is not committed

`repo2graph build` writes a `chunks.jsonl` that embeds each symbol's actual source text (see
[docs/reference.md](../docs/reference.md#what-one-piece-of-code-looks-like) — that is what makes it
useful for retrieval). For a repository this project does not own, shipping that file would mean
redistributing large, largely complete portions of someone else's source tree inside this project's
own repository. `examples/generate_examples.py` builds it transiently (to run the example queries
in `flows/`), then discards it — see the module docstring in
[`scripts/generate_examples.py`](../scripts/generate_examples.py). What *is* committed —
`nodes.jsonl.gz` (identifiers, paths, line ranges), `edges.jsonl.gz` (relationships), `overview.md`
(prose statistics), `stats.json`/`metadata.json` (counters) and `graph.html` (a diagram, not code) —
contains no source text at all. `flows/*.json` carries the results of the example queries with the
`text` field stripped from every chunk before it is written (see `run_queries` in the same script),
so the only source-shaped content anywhere under `examples/` is symbol *names* and *paths*, which
are facts about the repository's structure, not the expression the repository's license protects.

## Kubernetes

- **Repository:** https://github.com/kubernetes/kubernetes
- **Organization:** Cloud Native Computing Foundation (kubernetes)
- **License:** Apache-2.0
- **Analyzed commit:** see `examples/kubernetes/metadata.json` (`commit` field) — pinned, not "main"
- **Purpose of inclusion:** large-scale, single-language (Go) cloud-native monorepo; controller and
  scheduler architecture
- **Generated graph metadata committed:** yes (`nodes.jsonl.gz`, `edges.jsonl.gz`, statistics)
- **Source code copied:** no
- **Attribution requirement:** Apache-2.0 requires preserving copyright/license notices in
  *redistributed source*; none is redistributed here. This section is the attribution regardless.

## TensorFlow

- **Repository:** https://github.com/tensorflow/tensorflow
- **Organization:** Google / the TensorFlow project
- **License:** Apache-2.0
- **Analyzed commit:** see `examples/tensorflow/metadata.json`
- **Purpose of inclusion:** genuinely multi-language repository (Python API, C++ execution core);
  cross-language boundary resolution test case
- **Generated graph metadata committed:** yes
- **Source code copied:** no
- **Attribution requirement:** as above.

## Django

- **Repository:** https://github.com/django/django
- **Organization:** Django Software Foundation
- **License:** BSD-3-Clause
- **Analyzed commit:** see `examples/django/metadata.json`
- **Purpose of inclusion:** mature, long-lived, single-language Python framework; the pipeline's
  full-repository (non-scoped) baseline
- **Generated graph metadata committed:** yes
- **Source code copied:** no
- **Attribution requirement:** BSD-3-Clause requires preserving copyright notice, license text and
  disclaimer in redistributed source or binary form, and forbids using the Django Software
  Foundation's name to endorse derived products without permission. No source is redistributed here.

## VS Code

- **Repository:** https://github.com/microsoft/vscode
- **Organization:** Microsoft
- **License:** MIT
- **Analyzed commit:** see `examples/vscode/metadata.json`
- **Purpose of inclusion:** large single-language (TypeScript) application architecture; editor,
  workbench and platform services
- **Generated graph metadata committed:** yes
- **Source code copied:** no
- **Attribution requirement:** MIT requires preserving the copyright and permission notice in
  redistributed copies. No source is redistributed here.

## Linux kernel

- **Repository:** https://github.com/torvalds/linux
- **Organization:** Linus Torvalds / the Linux kernel community
- **License:** `GPL-2.0 WITH Linux-syscall-note` (as declared in the kernel's own `COPYING` file;
  individual files may carry other licenses under `LICENSES/`)
- **Analyzed commit:** see `examples/linux/metadata.json`
- **Purpose of inclusion:** extreme-scale, maximally heterogeneous C codebase; kernel subsystems, a
  filesystem, and a device driver as representative slices (see
  [docs/limitations.md](../docs/limitations.md#extreme-scale))
- **Generated graph metadata committed:** yes
- **Source code copied:** no
- **Attribution requirement:** GPL-2.0 governs *redistribution of the software itself*
  (source or binary) and does not restrict describing or analyzing it; no kernel source, in whole
  or in relevant part, is redistributed here, so GPL-2.0's copyleft obligations do not attach to
  this repository.
