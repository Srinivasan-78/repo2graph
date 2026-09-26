"""Named exclusion groups: generated, vendor, build, dependencies, sensitive.

One authored table, two consumers that used to disagree:

- `build --exclude-group NAME` turns a group into `--exclude` globs, so the
  paths never reach the graph at all;
- `doctor`'s "Generated / Vendored Code" check classifies paths that *did*
  reach the graph, and needs a human-readable reason per path rather than a
  glob.

Keeping the patterns in two places is how the doctor check ends up flagging a
shape that `--exclude-group` cannot actually exclude, so both derive from
`GROUPS` here.

**Glob anchoring.** `parse._glob_re` anchors a pattern containing "/" at the
repository root and lets a pattern without one match at any depth. So a
directory group must be written `**/node_modules/**`, not `node_modules/**` --
the latter misses `packages/web/node_modules/...`, which is where a monorepo
keeps all of it. `tests/test_determinism.py` asserts every glob below matches
its own representative paths, because a pattern that matches nothing is worse
than no pattern: the user runs it, nothing changes, and they conclude the
finding was wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExclusionGroup:
    """One named set of paths, expressed both as globs and as path shapes.

    Args:
        name: The `--exclude-group` value.
        description: One line, shown by `--exclude-group help`.
        globs: Patterns for `build --exclude`, in `parse._glob_re` syntax.
        dir_parts: Path segments whose presence marks membership. Used by
            `classify()` for reporting; the globs are what actually exclude.
        suffixes: Filename suffixes that mark membership.
        filenames: Exact filenames (compared lowercased).
        already_default: True when repo2graph's *defaults* already keep these
            out of an index, so naming the group changes nothing. Recorded so
            the docs and `--exclude-group help` cannot overclaim.
        representative: Paths the group must match. The determinism suite
            feeds these back through the real matcher.
    """

    name: str
    description: str
    globs: tuple[str, ...] = ()
    dir_parts: frozenset[str] = field(default_factory=frozenset)
    suffixes: tuple[str, ...] = ()
    filenames: frozenset[str] = field(default_factory=frozenset)
    already_default: bool = False
    representative: tuple[str, ...] = ()


GENERATED = ExclusionGroup(
    name="generated",
    description="machine-written source: protobuf/gRPC stubs, codegen output, minified bundles",
    globs=(
        "**/generated/**",
        "**/__generated__/**",
        "**/autogen/**",
        "*_pb2.py",
        "*_pb2_grpc.py",
        "*_pb.js",
        "*_pb.ts",
        "*.pb.go",
        "*.pb.cc",
        "*.pb.h",
        "*.pb.swift",
        "*_generated.go",
        "*_generated.ts",
        "*.generated.cs",
        "*.designer.cs",
        "*.g.dart",
        "*.freezed.dart",
        "*.min.js",
        "*.min.css",
        "*.bundle.js",
    ),
    dir_parts=frozenset({"generated", "__generated__", "autogen"}),
    suffixes=(
        "_pb2.py",
        "_pb2_grpc.py",
        "_pb.js",
        "_pb.ts",
        ".pb.go",
        ".pb.cc",
        ".pb.h",
        ".pb.swift",
        "_generated.go",
        "_generated.ts",
        ".generated.cs",
        ".designer.cs",
        ".g.dart",
        ".freezed.dart",
        ".min.js",
        ".min.css",
        ".bundle.js",
    ),
    representative=(
        "api/generated/client.py",
        "pkg/schema_pb2.py",
        "proto/user.pb.go",
        "web/static/app.min.js",
        "lib/models.freezed.dart",
    ),
)

VENDOR = ExclusionGroup(
    name="vendor",
    description="third-party source checked into the tree",
    globs=(
        "**/vendor/**",
        "**/third_party/**",
        "**/thirdparty/**",
        "**/Pods/**",
        "**/bower_components/**",
    ),
    dir_parts=frozenset({"vendor", "third_party", "thirdparty", "Pods", "bower_components"}),
    # `vendor` alone is in parse.DEFAULT_SKIP_DIRS; the other four are not, and
    # DEFAULT_SKIP_DIRS is a flat directory-name set rather than a glob, so it
    # cannot express "under any depth" the way these do.
    representative=(
        "vendor/github.com/pkg/errors/errors.go",
        "packages/api/vendor/lib.go",
        "third_party/zlib/zlib.c",
        "ios/Pods/Alamofire/Source/Alamofire.swift",
    ),
)

BUILD = ExclusionGroup(
    name="build",
    description="compiler and bundler output directories",
    globs=(
        "**/dist/**",
        "**/build/**",
        "**/out/**",
        "**/target/**",
        "**/.next/**",
        "**/.nuxt/**",
        "**/coverage/**",
        "**/__pycache__/**",
    ),
    dir_parts=frozenset(
        {"dist", "build", "out", "target", ".next", ".nuxt", "coverage", "__pycache__"}
    ),
    # Every one of these names is in parse.DEFAULT_SKIP_DIRS except "out", so
    # at the repository root this group is close to a no-op. It earns its keep
    # in a monorepo: DEFAULT_SKIP_DIRS matches a path *segment* anywhere, which
    # also means a legitimate source package literally named `build/` is
    # already invisible -- see docs/INDEXING.md.
    already_default=True,
    representative=(
        "dist/main.js",
        "packages/web/dist/main.js",
        "target/debug/app",
        "out/gen.go",
    ),
)

DEPENDENCIES = ExclusionGroup(
    name="dependencies",
    description="installed packages and lockfiles",
    globs=(
        "**/node_modules/**",
        "**/site-packages/**",
        "**/.venv/**",
        "**/venv/**",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "poetry.lock",
        "uv.lock",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
    ),
    dir_parts=frozenset({"node_modules", "site-packages", ".venv", "venv"}),
    filenames=frozenset(
        {
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "poetry.lock",
            "uv.lock",
            "cargo.lock",
            "composer.lock",
            "gemfile.lock",
            "go.sum",
        }
    ),
    representative=(
        "node_modules/react/index.js",
        "packages/web/node_modules/react/index.js",
        "package-lock.json",
        "services/api/go.sum",
    ),
)

SENSITIVE = ExclusionGroup(
    name="sensitive",
    description="credentials, keys and environment files",
    globs=(
        ".env",
        ".env.*",
        "*.pem",
        "*.key",
        "*.p12",
        "*.pfx",
        "*.keystore",
        "*.jks",
        "id_rsa*",
        "id_ed25519*",
        "**/secrets/**",
        "**/.aws/**",
        "**/.ssh/**",
        "**/.gnupg/**",
    ),
    dir_parts=frozenset({"secrets", ".aws", ".ssh", ".gnupg"}),
    suffixes=(".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"),
    # `repo2graph/secrets.py` already refuses these by default (and redacts
    # matches inside files it does index). This group is a second, *glob*-shaped
    # net for a tree that names its credentials something secrets.py does not
    # recognise -- not a replacement for it. `--include-secrets` disables that
    # default; it does not disable this group.
    already_default=True,
    representative=(
        ".env",
        "config/.env.production",
        "certs/server.pem",
        "deploy/secrets/db-password.txt",
    ),
)

GROUPS: dict[str, ExclusionGroup] = {
    g.name: g for g in (GENERATED, VENDOR, BUILD, DEPENDENCIES, SENSITIVE)
}

GROUP_NAMES: tuple[str, ...] = tuple(GROUPS)


def globs_for(names: list[str] | tuple[str, ...]) -> list[str]:
    """Every glob for the named groups, de-duplicated, in a stable order.

    "all" expands to every group. An unknown name raises ValueError naming the
    valid ones -- argparse `choices` already rejects it at the CLI, so this is
    for the Python API.
    """
    wanted: list[str] = []
    for name in names:
        if name == "all":
            wanted.extend(GROUP_NAMES)
        elif name in GROUPS:
            wanted.append(name)
        else:
            raise ValueError(
                f"unknown exclusion group {name!r}; choose from {', '.join(GROUP_NAMES)}, all"
            )

    seen: set[str] = set()
    out: list[str] = []
    for name in wanted:
        for glob in GROUPS[name].globs:
            if glob not in seen:
                seen.add(glob)
                out.append(glob)
    return out


def classify(rel: str) -> tuple[str, str] | None:
    """`(group name, human reason)` for a path, or None if it looks hand-written.

    Path-only: never reads the file. Directory segments are checked before the
    filename so `node_modules/foo/bar.min.js` reports as a dependency rather
    than as generated -- the actionable fix is excluding the directory, not
    the suffix.
    """
    parts = rel.split("/")
    for group in GROUPS.values():
        for part in parts[:-1]:
            if part in group.dir_parts:
                return group.name, f"under {part}/"

    name = parts[-1]
    lowered = name.lower()
    for group in GROUPS.values():
        if lowered in group.filenames:
            return group.name, "lockfile" if group is DEPENDENCIES else "by filename"
        for suffix in group.suffixes:
            if name.endswith(suffix):
                return group.name, f"*{suffix}"
    return None


def describe() -> str:
    """The table `--exclude-group help` prints."""
    lines = ["Exclusion groups for `repo2graph build --exclude-group NAME`:", ""]
    for group in GROUPS.values():
        default = "  (most already excluded by default)" if group.already_default else ""
        lines.append(f"  {group.name:<13} {group.description}{default}")
        lines.append(f"  {'':<13} {len(group.globs)} pattern(s), e.g. {', '.join(group.globs[:3])}")
    lines.append("")
    lines.append("  all           every group above")
    lines.append("")
    lines.append("Repeatable, and composable with --exclude. See docs/INDEXING.md.")
    return "\n".join(lines)
