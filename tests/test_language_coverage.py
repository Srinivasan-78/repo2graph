"""HCL, Objective-C, Vue, Svelte and Jupyter notebooks (#398).

Per the issue's acceptance: each fixture yields symbols of the expected kinds
and at least one in-repo CALLS edge, at the file's own line numbers.
"""

from __future__ import annotations

import json

import pytest

from repo2graph.chunks import iter_chunks
from repo2graph.cli import main
from repo2graph.export import path as artifact_path
from repo2graph.graph import build
from repo2graph.parse import component_script, notebook_code, refine_lang

ROOT_TF = """\
# Logs bucket.
resource "aws_s3_bucket" "logs" {
  bucket = "logs-${var.env}"
  tags   = merge(local.tags, { Name = "logs" })
  lifecycle {
    prevent_destroy = true
  }
}

variable "env" {
  type = string
}

module "vpc" {
  source = "./modules/vpc"
  cidr   = cidrsubnet("10.0.0.0/16", 4, 1)
}

locals {
  tags = { team = "infra" }
}

output "bucket_arn" {
  value = aws_s3_bucket.logs.arn
}

data "aws_iam_policy_document" "read" {}

resource "aws_s3_bucket_policy" "logs" {
  count  = 1
  bucket = aws_s3_bucket.logs.id
  policy = data.aws_iam_policy_document.read.json
  vpc    = module.vpc.id
}
"""

CACHE_M = """\
#import "Cache.h"

@protocol Store <NSObject>
- (id)load:(NSString *)key;
@end

@implementation Cache

- (id)load:(NSString *)key {
    return [self.items objectForKey:key];
}

- (id)fetch:(NSString *)key {
    id hit = [self load:key];
    return hit ?: normalize(key);
}

@end

static id normalize(id value) {
    return value;
}
"""

COUNTER_VUE = """\
<template>
  <button @click="increment">{{ count }}</button>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { clamp } from "./math";

const count = ref(0);

function increment(): void {
  count.value = clamp(count.value + 1);
}
</script>

<style scoped>
button { color: red; }
</style>
"""

TOGGLE_SVELTE = """\
<script>
  import { clamp } from "./math";
  let on = false;

  function toggle() {
    on = !on;
    report(clamp(1));
  }

  function report(n) {
    console.log(n);
  }
</script>

<button on:click={toggle}>{on ? "on" : "off"}</button>
"""


def _notebook(cells: list[dict]) -> str:
    nb = {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    # nbformat's own layout: indent 1, sorted keys, one source line per string.
    return json.dumps(nb, indent=1, sort_keys=True) + "\n"


def _code(*lines: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": 1,
        "metadata": {},
        "outputs": [{"output_type": "stream", "name": "stdout", "text": ['"source": [\n']}],
        "source": list(lines),
    }


NOTEBOOK = _notebook(
    [
        {"cell_type": "markdown", "metadata": {}, "source": ["# Title\n", "def not_code(): pass"]},
        _code(
            "%matplotlib inline\n",
            "!pip install numpy\n",
            "import math\n",
            "\n",
            "def area(r):\n",
            "    return math.pi * square(r)\n",
        ),
        _code("def square(x):\n", '    """Square it."""\n', "    return x * x\n", "\n", "area(2)"),
    ]
)


def _write(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf8")
    return root


def _line_of(text: str, needle: str) -> int:
    return next(i for i, ln in enumerate(text.split("\n"), 1) if needle in ln)


def _calls(g) -> set[tuple[str, str]]:
    return {(e["src"], e["dst"]) for e in g.edges if e["type"] == "CALLS"}


def _imports(g) -> set[tuple[str, str]]:
    return {(e["src"], e["dst"]) for e in g.edges if e["type"] == "IMPORTS"}


# --- HCL / Terraform --------------------------------------------------------


@pytest.fixture
def tf(tmp_path):
    return build(
        _write(
            tmp_path,
            {
                "infra/main.tf": ROOT_TF,
                "infra/modules/vpc/main.tf": (
                    'variable "cidr" {}\n'
                    'resource "aws_vpc" "this" {\n  cidr_block = var.cidr\n}\n'
                    'output "id" { value = aws_vpc.this.id }\n'
                ),
            },
        )
    )


def test_hcl_blocks_are_symbols_named_by_terraform_address(tf):
    syms = {
        n["qualname"]: n
        for n in tf.nodes.values()
        if n["type"] == "symbol" and n["path"] == "infra/main.tf"
    }
    assert {q: n["kind"] for q, n in syms.items()} == {
        "aws_s3_bucket.logs": "resource",
        "var.env": "variable",
        "module.vpc": "module",
        "local.tags": "local",
        "output.bucket_arn": "output",
        "data.aws_iam_policy_document.read": "data",
        "aws_s3_bucket_policy.logs": "resource",
    }
    # Nested blocks belong to their resource; they are not symbols.
    assert syms["aws_s3_bucket.logs"]["start_line"] == _line_of(ROOT_TF, 'resource "aws_s3_bucket"')
    assert syms["aws_s3_bucket.logs"]["end_line"] == _line_of(ROOT_TF, "tags   = merge") + 4


def test_hcl_references_between_blocks_are_calls(tf):
    s = "sym:infra/main.tf::"
    calls = _calls(tf)
    for src, dst in [
        ("aws_s3_bucket.logs", "var.env"),
        ("aws_s3_bucket.logs", "local.tags"),
        ("output.bucket_arn", "aws_s3_bucket.logs"),
        ("aws_s3_bucket_policy.logs", "aws_s3_bucket.logs"),
        ("aws_s3_bucket_policy.logs", "data.aws_iam_policy_document.read"),
        ("aws_s3_bucket_policy.logs", "module.vpc"),
    ]:
        assert (s + src, s + dst) in calls
    # `count.index`-style meta references name no block.
    assert not any(dst.endswith("::count") for _src, dst in calls)
    m = "sym:infra/modules/vpc/main.tf::"
    assert (m + "output.id", m + "aws_vpc.this") in calls


def test_hcl_local_module_source_imports_its_main_tf(tf):
    assert ("file:infra/main.tf", "file:infra/modules/vpc/main.tf") in _imports(tf)


# --- Objective-C --------------------------------------------------------------


def test_objc_classes_methods_and_message_sends(tmp_path):
    g = build(
        _write(
            tmp_path,
            {
                "Cache.m": CACHE_M,
                "Cache.h": (
                    "#import <Foundation/Foundation.h>\n"
                    "@interface Cache : NSObject <Store>\n- (id)fetch:(NSString *)key;\n@end\n"
                ),
            },
        )
    )
    kinds = {
        (n["path"], n["qualname"]): n["kind"] for n in g.nodes.values() if n["type"] == "symbol"
    }
    assert kinds[("Cache.m", "Store")] == "protocol"
    assert kinds[("Cache.m", "Cache.load")] == "method"
    assert kinds[("Cache.m", "normalize")] == "function"
    assert kinds[("Cache.h", "Cache")] == "class"
    assert g.nodes["file:Cache.h"]["lang"] == "objc"  # sniffed from `@interface`
    calls = _calls(g)
    assert ("sym:Cache.m::Cache.fetch", "sym:Cache.m::Cache.load") in calls  # [self load:]
    assert ("sym:Cache.m::Cache.fetch", "sym:Cache.m::normalize") in calls
    assert ("file:Cache.m", "file:Cache.h") in _imports(g)
    # `@interface Cache : NSObject <Store>`: the in-repo protocol is implemented.
    assert {(e["dst"], e["subtype"]) for e in g.edges if e["src"] == "sym:Cache.h::Cache"} >= {
        ("sym:Cache.m::Store", "IMPLEMENTS")
    }


def test_objc_m_file_without_objc_signals_is_matlab_text(tmp_path):
    g = build(_write(tmp_path, {"plot.m": "x = [1 2 3];\nfunction y = f(x)\n  y = x * 2;\nend\n"}))
    assert g.nodes["file:plot.m"]["file_type"] == "other"
    assert not [n for n in g.nodes.values() if n["type"] == "symbol"]
    assert refine_lang("objc", ".m", b"#import <UIKit/UIKit.h>\n")[0] == "objc"
    assert refine_lang("objc", ".mm", b"x = 1;\n")[0] == "objc"


# --- Vue and Svelte -------------------------------------------------------------


def test_vue_script_setup_ts_is_parsed_at_the_component_lines(tmp_path):
    g = build(
        _write(
            tmp_path,
            {
                "web/Counter.vue": COUNTER_VUE,
                "web/math.js": "export function clamp(n) {\n  return n;\n}\n",
            },
        )
    )
    sym = g.nodes["sym:web/Counter.vue::increment"]
    assert sym["kind"] == "function"
    assert sym["start_line"] == _line_of(COUNTER_VUE, "function increment")
    assert g.nodes["file:web/Counter.vue"]["lang"] == "typescript"
    assert ("sym:web/Counter.vue::increment", "sym:web/math.js::clamp") in _calls(g)
    assert ("file:web/Counter.vue", "file:web/math.js") in _imports(g)


def test_svelte_script_is_parsed_and_its_calls_resolve(tmp_path):
    g = build(
        _write(
            tmp_path,
            {
                "Toggle.svelte": TOGGLE_SVELTE,
                "math.js": "export function clamp(n) {\n  return n;\n}\n",
                "App.vue": '<script>\nimport Toggle from "./Toggle.svelte";\n</script>\n',
            },
        )
    )
    assert g.nodes["file:Toggle.svelte"]["lang"] == "javascript"
    calls = _calls(g)
    assert ("sym:Toggle.svelte::toggle", "sym:Toggle.svelte::report") in calls
    assert ("sym:Toggle.svelte::toggle", "sym:math.js::clamp") in calls
    assert ("file:App.vue", "file:Toggle.svelte") in _imports(g)


def test_component_script_keeps_every_line_and_drops_the_markup():
    lang, code = component_script(COUNTER_VUE.encode())
    text = code.decode()
    assert lang == "typescript"
    assert text.count("\n") == COUNTER_VUE.count("\n")
    assert "button" not in text and "color: red" not in text
    assert component_script(b"<template><p/></template>\n")[1] == b"\n"


# --- Jupyter notebooks ------------------------------------------------------------


def test_notebook_code_cells_keep_their_lines_and_name_the_cell(tmp_path):
    g = build(_write(tmp_path, {"nb/analysis.ipynb": NOTEBOOK}))
    area = g.nodes["sym:nb/analysis.ipynb::area"]
    assert area["start_line"] == _line_of(NOTEBOOK, '"def area(r):')
    assert ("sym:nb/analysis.ipynb::area", "sym:nb/analysis.ipynb::square") in _calls(g)
    assert g.nodes["sym:nb/analysis.ipynb::square"]["docstring"] == "Square it."
    assert "sym:nb/analysis.ipynb::not_code" not in g.nodes  # markdown is not code

    chunks = {c["id"]: c["text"] for c in iter_chunks(g)}
    assert "# notebook cell 2" in chunks["sym:nb/analysis.ipynb::area"]
    assert "# notebook cell 3" in chunks["sym:nb/analysis.ipynb::square"]
    assert "def area(r):\n    return math.pi * square(r)" in chunks["sym:nb/analysis.ipynb::area"]
    whole = chunks["file:nb/analysis.ipynb"]
    assert '"cell_type"' not in whole and "outputs" not in whole


def test_notebook_magics_markdown_and_outputs_are_not_code():
    text = notebook_code(NOTEBOOK.encode()).decode()
    assert text.count("\n") == NOTEBOOK.count("\n")
    assert "%matplotlib" not in text and "pip install" not in text
    assert "not_code" not in text  # a markdown cell
    assert "# cell 1" not in text  # cell 1 is markdown: no marker
    assert text.split("\n")[_line_of(NOTEBOOK, '"import math') - 1] == "import math"


def test_a_minified_notebook_yields_no_code_rather_than_wrong_lines():
    minified = json.dumps(json.loads(NOTEBOOK))
    assert notebook_code(minified.encode()).strip() == b""


def test_incremental_rebuild_matches_full_for_the_new_formats(tmp_path):
    repo = _write(
        tmp_path / "repo",
        {
            "main.tf": ROOT_TF,
            "Cache.m": CACHE_M,
            "Counter.vue": COUNTER_VUE,
            "Toggle.svelte": TOGGLE_SVELTE,
            "analysis.ipynb": NOTEBOOK,
        },
    )
    full, inc = tmp_path / "full", tmp_path / "inc"
    main(["build", str(repo), "-o", str(full), "--formats", "jsonl"])
    main(["build", str(repo), "-o", str(inc), "--formats", "jsonl", "--incremental"])
    main(["build", str(repo), "-o", str(inc), "--formats", "jsonl", "--incremental"])
    for name in ("nodes.jsonl", "edges.jsonl", "chunks.jsonl"):
        assert artifact_path(full, name).read_bytes() == artifact_path(inc, name).read_bytes()
