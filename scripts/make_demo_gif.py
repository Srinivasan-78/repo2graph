#!/usr/bin/env python3
"""Render the README terminal GIFs from real command output.

Every frame in `docs/images/*.gif` is produced here, and the text in them is
whatever the installed `repo2graph` actually printed when this script last ran
-- nothing is typed by hand into a mockup. That is the point: a demo GIF that
drifts from the CLI is worse than no GIF, so regenerating is a one-liner.

    python scripts/make_demo_gif.py                # all scenes
    python scripts/make_demo_gif.py rag            # one scene
    python scripts/make_demo_gif.py --repo . --keep-index

Requires Pillow (`pip install pillow`); it is a dev-only dependency and is not
imported anywhere in the package itself.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - dev tool
    sys.exit("Pillow is required: pip install pillow")

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"

# --- look -------------------------------------------------------------------
# Sampled off the original demo.gif so new scenes sit beside it without a seam.
WIDTH, HEIGHT = 900, 460
TITLEBAR_H = 40
PAD_X, PAD_Y = 20, 52
LINE_H = 22
FONT_SIZE = 15

BG = (13, 17, 23)
TITLEBAR = (22, 27, 34)
TITLE_FG = (139, 148, 158)
FG = (201, 209, 217)
DIM = (139, 148, 158)
BLUE = (88, 166, 255)
GREEN = (63, 185, 80)
YELLOW = (210, 153, 34)
PURPLE = (188, 140, 255)
LIGHTS = ((255, 95, 86), (255, 189, 46), (39, 201, 63))

FRAME_MS = 70
RAMP_STEPS = 6  # anti-aliasing steps kept per text colour
TYPE_CHARS_PER_FRAME = 3
HOLD_FRAMES_END = 26
HOLD_FRAMES_AFTER_CMD = 6

FONT_CANDIDATES = (
    "consola.ttf",
    "DejaVuSansMono.ttf",
    "LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
)
BOLD_CANDIDATES = (
    "consolab.ttf",
    "DejaVuSansMono-Bold.ttf",
    "LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
)

ANSI = re.compile(r"\x1b\[[0-9;]*m")
# `- CALLS in: ...` / `- DEFINES out: ...` as printed by repo_neighbours.
EDGE_LINE = re.compile(r"- ([A-Z_]+ (?:in|out)): ")


def _load_font(candidates, size):
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT = _load_font(FONT_CANDIDATES, FONT_SIZE)
FONT_BOLD = _load_font(BOLD_CANDIDATES, FONT_SIZE)
CHAR_W = FONT.getlength("M") or 9
MAX_COLS = int((WIDTH - 2 * PAD_X) / CHAR_W)
MAX_ROWS = int((HEIGHT - PAD_Y - 12) / LINE_H)


# --- colouring --------------------------------------------------------------
def colorize(line: str):
    """Split one output line into (text, colour, bold) runs.

    Deliberately crude: it keys off the shapes repo2graph's own output uses
    (`$ ` prompts, `#` annotations, `--- id [why]` and `### [cite: ...]`
    headers) rather than pretending to be a syntax highlighter.
    """
    if line.startswith("$ "):
        return [("$", FG, True), (line[1:], FG, True)]
    if line.startswith("→ "):
        return [("→", PURPLE, True), (line[1:], FG, True)]
    if line.startswith("### [cite:"):
        close = line.find("]")
        return [
            ("### ", DIM, False),
            (line[4 : close + 1], GREEN, True),
            (line[close + 1 :], FG, False),
        ]
    if line.startswith("--- "):
        rest = line[4:]
        tag = rest.find(" [")
        if tag == -1:
            return [("--- ", DIM, False), (rest, BLUE, False)]
        return [
            ("--- ", DIM, False),
            (rest[:tag], BLUE, False),
            (rest[tag:], DIM, False),
        ]
    if line.startswith("#"):
        head = line.split(":", 1)
        if len(head) == 2 and len(head[0]) < 28:
            return [(head[0] + ":", DIM, True), (head[1], DIM, False)]
        return [(line, DIM, False)]
    edge = EDGE_LINE.match(line)
    if edge:
        rest = line[edge.end() :]
        node = rest.find(" [sym:")
        head = [("- ", DIM, False), (edge.group(1), PURPLE, True), (": ", DIM, False)]
        if node == -1:
            return [*head, (rest, FG, False)]
        return [*head, (rest[:node], FG, False), (rest[node:], DIM, False)]
    if line.startswith(("- ", "| ")):
        return [(line, DIM, False)]
    if line.startswith("->"):
        return [(line[:2], PURPLE, True), (line[2:], FG, False)]
    stripped = line.lstrip()
    if stripped.startswith('"') and '":' in stripped:
        indent = line[: len(line) - len(stripped)]
        key, _, value = stripped.partition('":')
        return [
            (indent + key + '"', FG, False),
            (":", DIM, False),
            (value, YELLOW, False),
        ]
    if line.startswith(("{", "}")):
        return [(line, DIM, False)]
    return [(line, FG, False)]


def draw_frame(lines, cursor=False, title="repo2graph"):
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH, TITLEBAR_H], fill=TITLEBAR)
    for i, colour in enumerate(LIGHTS):
        cx = 26 + i * 24
        d.ellipse([cx - 6, 14, cx + 6, 26], fill=colour)
    tw = FONT.getlength(title)
    d.text(((WIDTH - tw) / 2, 11), title, font=FONT, fill=TITLE_FG)

    y = PAD_Y
    for line in lines[-MAX_ROWS:]:
        x = PAD_X
        for text, colour, bold in colorize(line):
            f = FONT_BOLD if bold else FONT
            d.text((x, y), text, font=f, fill=colour)
            x += f.getlength(text)
        y += LINE_H
    if cursor:
        x = PAD_X + FONT.getlength(lines[-1]) if lines else PAD_X
        d.rectangle([x + 1, y - LINE_H + 2, x + 8, y - 4], fill=FG)
    return img


def build_frames(scenes):
    """scenes: list of (command, output_lines[, prompt]). Returns frames.

    The prompt is per scene because not every scene is a shell: an MCP tool
    call is not something anyone types at a `$`, and drawing it as one would
    teach the reader the wrong thing about how the server is driven.
    """
    frames = []
    screen: list[str] = []
    for scene in scenes:
        command, output = scene[0], scene[1]
        prompt = scene[2] if len(scene) > 2 else "$ "
        screen.append("")
        prompt_row = len(screen)
        screen.append(prompt)
        for i in range(0, len(command) + 1, TYPE_CHARS_PER_FRAME):
            screen[prompt_row] = prompt + command[:i]
            frames.append(draw_frame(screen, cursor=True))
        screen[prompt_row] = prompt + command
        frames.extend([draw_frame(screen, cursor=True)] * HOLD_FRAMES_AFTER_CMD)
        screen.append("")
        for line in output:
            screen.append(line)
            frames.append(draw_frame(screen))
    frames.extend([draw_frame(screen)] * HOLD_FRAMES_END)
    return frames


def master_palette():
    """A fixed palette: the UI colours plus an anti-aliasing ramp for each.

    Quantizing adaptively would spend the whole table on text greys and merge
    the three window lights -- 113 pixels each -- into one muddy dot. Naming
    the colours instead keeps them exact and makes every frame's palette
    identical, which is what lets GIF store later frames as deltas.
    """
    colours = [BG, TITLEBAR, *LIGHTS]
    for fg in (FG, DIM, BLUE, GREEN, YELLOW, PURPLE, TITLE_FG):
        for step in range(1, RAMP_STEPS + 1):
            t = step / RAMP_STEPS
            colours.append(tuple(round(BG[i] + (fg[i] - BG[i]) * t) for i in range(3)))
    flat = [c for colour in colours for c in colour]
    flat += [0] * (768 - len(flat))
    palette = Image.new("P", (1, 1))
    palette.putpalette(flat)
    return palette


def save_gif(frames, path: Path):
    """Write the frames as one GIF, small enough to sit in a README.

    Two things do the work. Every frame is quantized against a single palette
    taken from the last (busiest) frame, so consecutive frames share colour
    indices and GIF's own delta coding can fire -- a per-frame adaptive palette
    re-encodes the whole canvas each time and roughly triples the file. And
    runs of identical frames (the typing pause, the hold at the end) collapse
    into one frame with a longer delay instead of dozens of copies.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    master = master_palette()
    quantized = [f.quantize(palette=master, dither=Image.NONE) for f in frames]

    kept, durations = [], []
    for frame in quantized:
        if kept and frame.tobytes() == kept[-1].tobytes():
            durations[-1] += FRAME_MS
        else:
            kept.append(frame)
            durations.append(FRAME_MS)

    kept[0].save(
        path,
        save_all=True,
        append_images=kept[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    return path.stat().st_size


# --- running the real thing -------------------------------------------------
def run(args: list[str], cwd: Path | None = None) -> list[str]:
    """Run a command and return its stdout as display lines.

    `PYTHONIOENCODING` is forced because a piped stdout on Windows encodes to
    cp1252, which turns every em dash in the CLI's own output into `?`.
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8", NO_COLOR="1")
    proc = subprocess.run(args, cwd=cwd, capture_output=True, env=env, timeout=600, check=False)
    text = proc.stdout.decode("utf8", "replace")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf8", "replace"))
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(args)}")
    return fit(text)


def fit(text: str) -> list[str]:
    out = []
    for raw in ANSI.sub("", text).split("\n"):
        line = raw.rstrip("\r").replace("\t", "    ")
        out.append(line if len(line) <= MAX_COLS else line[: MAX_COLS - 1] + "…")
    while out and not out[-1].strip():
        out.pop()
    return out


def cli(*args: str) -> list[str]:
    exe = shutil.which("repo2graph")
    return [exe, *args] if exe else [sys.executable, "-m", "repo2graph.cli", *args]


def take(lines: list[str], n: int) -> list[str]:
    return lines[:n]


BUILD_ARGS = ("build", "--exclude", "scripts/*", "--git-history", "300")


def elide_map(lines: list[str], keep_head: int = 4) -> list[str]:
    """Keep the head of a pack, then cut to the first cited block.

    A pack leads with the repo map, which for a real repository is longer than
    the frame -- so the citations, the part worth seeing, would never scroll
    into view. The cut is marked with a literal `…` rather than hidden.
    """
    for i, line in enumerate(lines):
        if line.startswith("### [cite:"):
            if i <= keep_head + 1:
                return lines
            return [*lines[:keep_head], "", "…", "", *lines[i:]]
    return lines


# --- scenes -----------------------------------------------------------------
def scene_rag(repo: Path, index: Path):
    """Ask a question, get cited code back -- the one-screen pitch.

    The output is shown unfiltered and simply scrolls: the repo map goes by
    first and the `[cite: ...]` blocks land on the closing frames, which is
    both what the terminal does and the half worth freezing on.
    """
    q = "where is the pack token budget enforced"
    out = run(cli("rag", q, "--out", str(index), "--budget", "2000"))
    return [(f'repo2graph rag "{q}" --budget 2000', take(out, 34))]


def scene_build(repo: Path, index: Path):
    """Point it at a folder; no setup step, no language server.

    This one runs verbatim what the caption claims -- no `--exclude` -- because
    nothing from `scripts/` can reach a stats block anyway.
    """
    out = run(cli("build", str(repo), "--git-history", "300", "--out", str(index)))
    keep = [
        ln
        for ln in out
        if any(
            k in ln
            for k in (
                '"files"',
                '"nodes"',
                '"edges"',
                '"chunks"',
                '"symbol:function"',
                '"symbol:class"',
                '"edge:CALLS"',
                '"edge:IMPORTS"',
                '"edge:CO_CHANGE"',
                '"parsed"',
            )
        )
    ]
    # An excerpt: the real stats block is ~20 keys across two nesting levels,
    # so the interesting ones are picked out and re-indented to one level.
    body = ["    " + ln.strip() for ln in take(keep, 11)]
    return [("repo2graph build . --git-history 300 --out .r2g", ["{", *body, "}"])]


def scene_mcp(repo: Path, index: Path):
    """What an MCP client gets back from one `repo_neighbours` call.

    `repo_neighbours` rather than `repo_search` on purpose: the search result
    is a pack that leads with the repo map and scrolls, while the hop from one
    symbol to its callers, callees and definer is the thing grep has no answer
    for at all, and it fits on one screen.
    """
    sys.path.insert(0, str(ROOT))
    from repo2graph.mcp import dispatch, open_index

    index_obj = open_index(str(index), repo=str(repo))
    args = {
        "node_id": "sym:repo2graph/query.py::Index.pack_context",
        "hops": 1,
        "limit": 14,
    }
    lines = fit(dispatch(index_obj, "repo_neighbours", args))
    pretty = 'repo_neighbours {"node_id": "sym:…::Index.pack_context", "hops": 1}'
    return [(pretty, take(lines, 16), "→ ")]


SCENES = {
    "rag": (scene_rag, "demo-rag.gif"),
    "build": (scene_build, "demo-build.gif"),
    "mcp": (scene_mcp, "demo-mcp.gif"),
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenes", nargs="*", choices=[*SCENES, []], help="default: all")
    ap.add_argument("--repo", default=str(ROOT), help="repository to demo against")
    ap.add_argument("--out-dir", default=str(IMAGES))
    ap.add_argument("--keep-index", action="store_true")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    out_dir = Path(args.out_dir)
    wanted = args.scenes or list(SCENES)

    index = Path(tempfile.mkdtemp(prefix="r2g-demo-"))
    try:
        # `scripts/*` is excluded so a demo run cannot retrieve this generator
        # and star in its own GIF; `--git-history` is what puts CO_CHANGE in
        # the stats, and those edges are half of why the graph beats grep.
        run(cli(*BUILD_ARGS, str(repo), "--out", str(index)))
        for name in wanted:
            fn, filename = SCENES[name]
            frames = build_frames(fn(repo, index))
            size = save_gif(frames, out_dir / filename)
            print(f"{filename}: {len(frames)} frames, {size / 1024:.0f} KiB")
    finally:
        if not args.keep_index:
            shutil.rmtree(index, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
