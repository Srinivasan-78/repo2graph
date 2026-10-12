# Attributions

Every repository under `examples/` is someone else's copyrighted work. This file records what was
analyzed, under what license, what was and was not copied out of it, and what attribution each
license requires. License identifiers below were pulled from each repository's own license file
(`COPYING`/`LICENSE`) or GitHub's license API on 2026-09-17 — not assumed. The six repositories
added on 2026-10-11 were checked the same way, from the license files at each pinned commit.

## Why `chunks.jsonl` is not committed

`repo2graph build` writes a `chunks.jsonl` that embeds each symbol's actual source text (see
[examples/README.md](README.md) — that is what makes it
useful for retrieval). For a repository this project does not own, shipping that file would mean
redistributing large, largely complete portions of someone else's source tree inside this project's
own repository. `scripts/generate_examples.py` builds it transiently (to run the example queries),
then discards it — see the module docstring in
[`scripts/generate_examples.py`](../scripts/generate_examples.py).

What is committed for each repository is two prose files: `README.md` (the pinned commit, the scope,
and counters) and `overview.md` (the prose repo map). Neither contains source text. The generator
also writes `nodes.jsonl.gz`, `edges.jsonl.gz`, `manifest.json`, `stats.json`, `graph.html` and
`flows/*.json`, and those are deliberately *not* committed either — they were dropped in `4e96b628`
for size, not for licensing, and they were already source-free by construction: the first two carry
identifiers, paths and line ranges, and `flows/*.json` has the `text` field stripped from every
chunk before it is written (see `run_queries` in the same script). So the only source-shaped content
anywhere under `examples/` is symbol *names* and *paths*, which are facts about the repository's
structure, not the expression the repository's license protects.

## Kubernetes

- **Repository:** https://github.com/kubernetes/kubernetes
- **Organization:** Cloud Native Computing Foundation (kubernetes)
- **License:** Apache-2.0
- **Analyzed commit:** see `examples/kubernetes/README.md`, "Revision" — pinned, not "main"
- **Purpose of inclusion:** large-scale, single-language (Go) cloud-native monorepo; controller and
  scheduler architecture
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** Apache-2.0 requires preserving copyright/license notices in
  *redistributed source*; none is redistributed here. This section is the attribution regardless.

## TensorFlow

- **Repository:** https://github.com/tensorflow/tensorflow
- **Organization:** Google / the TensorFlow project
- **License:** Apache-2.0
- **Analyzed commit:** see `examples/tensorflow/README.md`, "Revision"
- **Purpose of inclusion:** genuinely multi-language repository (Python API, C++ execution core);
  cross-language boundary resolution test case
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** as above.

## Django

- **Repository:** https://github.com/django/django
- **Organization:** Django Software Foundation
- **License:** BSD-3-Clause
- **Analyzed commit:** see `examples/django/README.md`, "Revision"
- **Purpose of inclusion:** mature, long-lived, single-language Python framework; the pipeline's
  full-repository (non-scoped) baseline
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** BSD-3-Clause requires preserving copyright notice, license text and
  disclaimer in redistributed source or binary form, and forbids using the Django Software
  Foundation's name to endorse derived products without permission. No source is redistributed here.

## VS Code

- **Repository:** https://github.com/microsoft/vscode
- **Organization:** Microsoft
- **License:** MIT
- **Analyzed commit:** see `examples/vscode/README.md`, "Revision"
- **Purpose of inclusion:** large single-language (TypeScript) application architecture; editor,
  workbench and platform services
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** MIT requires preserving the copyright and permission notice in
  redistributed copies. No source is redistributed here.

## Linux kernel

- **Repository:** https://github.com/torvalds/linux
- **Organization:** Linus Torvalds / the Linux kernel community
- **License:** `GPL-2.0 WITH Linux-syscall-note` (as declared in the kernel's own `COPYING` file;
  individual files may carry other licenses under `LICENSES/`)
- **Analyzed commit:** see `examples/linux/README.md`, "Revision"
- **Purpose of inclusion:** extreme-scale, maximally heterogeneous C codebase; kernel subsystems, a
  filesystem, and a device driver as representative slices (see
  [what it can't do](../README.md#what-it-cant-do))
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** GPL-2.0 governs *redistribution of the software itself*
  (source or binary) and does not restrict describing or analyzing it; no kernel source, in whole
  or in relevant part, is redistributed here, so GPL-2.0's copyleft obligations do not attach to
  this repository.

## React

- **Repository:** https://github.com/facebook/react
- **Organization:** Meta Platforms, Inc. and affiliates
- **License:** MIT
- **Analyzed commit:** see `examples/react/README.md`, "Revision"
- **Purpose of inclusion:** recognisable JavaScript monorepo; also the gallery's record of what Flow
  type annotations cost a parser that has no Flow grammar
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** MIT requires preserving the copyright and permission notice in
  redistributed copies. No source is redistributed here.

## FastAPI

- **Repository:** https://github.com/fastapi/fastapi
- **Organization:** Sebastián Ramírez and the FastAPI contributors
- **License:** MIT
- **Analyzed commit:** see `examples/fastapi/README.md`, "Revision"
- **Purpose of inclusion:** modern async Python framework, indexed whole with history; also one of
  the four repositories in the retrieval benchmark (`benchmarks/real/`)
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** as for React (MIT).

## Gin

- **Repository:** https://github.com/gin-gonic/gin
- **Organization:** Manuel Martínez-Almeida and the Gin contributors
- **License:** MIT
- **Analyzed commit:** see `examples/gin/README.md`, "Revision"
- **Purpose of inclusion:** small, widely used Go web framework, indexed whole with history — the
  counterpoint to the scoped Kubernetes slice
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** as for React (MIT).

## ripgrep

- **Repository:** https://github.com/BurntSushi/ripgrep
- **Organization:** Andrew Gallant (BurntSushi) and the ripgrep contributors
- **License:** `Unlicense OR MIT` — dual-licensed, as the repository's `COPYING` states, with the
  texts in `UNLICENSE` and `LICENSE-MIT`
- **Analyzed commit:** see `examples/ripgrep/README.md`, "Revision"
- **Purpose of inclusion:** the gallery's Rust entry, a Cargo workspace of reusable crates; also the
  tool the retrieval benchmark's grep baseline runs
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** under the Unlicense, none; under MIT, preserving the copyright and
  permission notice in redistributed copies. No source is redistributed here.

## Guava

- **Repository:** https://github.com/google/guava
- **Organization:** Google / the Guava project
- **License:** Apache-2.0
- **Analyzed commit:** see `examples/guava/README.md`, "Revision"
- **Purpose of inclusion:** the gallery's Java entry; an interface-heavy library where name-based
  call resolution meets many same-named candidates
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** as for Kubernetes (Apache-2.0).

## Jekyll

- **Repository:** https://github.com/jekyll/jekyll
- **Organization:** Tom Preston-Werner and the Jekyll contributors
- **License:** MIT
- **Analyzed commit:** see `examples/jekyll/README.md`, "Revision"
- **Purpose of inclusion:** the gallery's Ruby entry, indexed whole with history; plugin wiring
  through subclass registration
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** as for React (MIT).

## Rails

- **Repository:** https://github.com/rails/rails
- **Organization:** David Heinemeier Hansson and the Rails contributors
- **License:** MIT (declared in the repository's own `MIT-LICENSE`)
- **Analyzed commit:** see `examples/rails/README.md`, "Revision"
- **Purpose of inclusion:** the gallery's scoped Ruby entry, and the clearest case of a codebase
  that builds its own API at load time — `included do`, `delegate`, `define_method` and
  `method_missing` leave a name-based call graph with no call site to find
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** as for React (MIT).

## Spring Framework

- **Repository:** https://github.com/spring-projects/spring-framework
- **Organization:** VMware / Broadcom and the Spring contributors
- **License:** Apache-2.0 (declared in the repository's own `LICENSE.txt`)
- **Analyzed commit:** see `examples/spring-framework/README.md`, "Revision"
- **Purpose of inclusion:** the gallery's second Java entry; dependency injection puts the choice
  of implementation in annotations and component scans rather than in a call, so the graph maps the
  container's machinery and not the wiring it performs
- **Generated artifacts committed:** prose only (`README.md`, `overview.md`)
- **Source code copied:** no
- **Attribution requirement:** as for Kubernetes (Apache-2.0).
