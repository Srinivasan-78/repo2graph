"""Every definition the parser finds keeps its own symbol node and chunk.

A node id is `sym:<path>::<qualname>`, so two definitions sharing a qualname
in one file (overloads, conditional redefinition, Go methods on different
receivers before they were receiver-qualified) used to collapse into one node
and every body but one vanished from method-level chunks. Kotlin `fun`s were
never indexed at all. These tests pin hand-derived ids per language.
"""

import re
from pathlib import Path

import pytest

from repo2graph.chunks import build_chunks
from repo2graph.graph import build, cache_entry, entry_read
from repo2graph.parse import LANG_CFG, parse_source

# One fixture per LANG_CFG language: a class/container with methods, a
# top-level function and, where the language allows, a same-name pair.
FIXTURES = {
    "python": (
        "m.py",
        """class A:
    def run(self):
        return "mk_run1"

    def step(self):
        return "mk_step"

    def run(self):
        return "mk_run2"


def top():
    return "mk_top"
""",
    ),
    "javascript": (
        "m.js",
        """class A {
  run() { return "mk_run"; }
  step() { return "mk_step"; }
}
function top() { return "mk_top1"; }
function top() { return "mk_top2"; }
""",
    ),
    "typescript": (
        "m.ts",
        """class A {
  run(x: string): void;
  run(x: number): void;
  run(x: any): void { console.log("mk_run"); }
  step(): void { console.log("mk_step"); }
}
function top(a: string): void;
function top(a: any): void { console.log("mk_top"); }
function other(): void { console.log("mk_other1"); }
""",
    ),
    "tsx": (
        "m.tsx",
        """class A {
  run(): void { console.log("mk_run"); }
  step(): void { console.log("mk_step"); }
}
function top(): void { console.log("mk_top1"); }
function top(): void { console.log("mk_top2"); }
""",
    ),
    "go": (
        "m.go",
        """package m

type A struct{}
type B struct{}

func (a *A) Run() { a.step(); println("mk_arun") }
func (a *A) step() { println("mk_astep") }
func (b B) Run() { println("mk_brun") }
func Top() { println("mk_top") }
""",
    ),
    "rust": (
        "m.rs",
        """struct A {}
impl A {
    fn run(&self) { println!("mk_run"); }
    fn step(&self) { println!("mk_step"); }
}
fn top() { println!("mk_top"); }
""",
    ),
    "java": (
        "A.java",
        """class A {
    void run() { System.out.println("mk_run1"); }
    void run(int x) { System.out.println("mk_run2"); }
    void step() { System.out.println("mk_step"); }
}
""",
    ),
    "ruby": (
        "m.rb",
        """class A
  def run
    puts "mk_run1"
  end
  def step
    puts "mk_step"
  end
  def run
    puts "mk_run2"
  end
end
def top
  puts "mk_top"
end
""",
    ),
    "c": (
        "m.c",
        """struct A { int x; };
static int step(int x) { return x + 1; /* mk_step */ }
int top(void) { return step(1); /* mk_top */ }
""",
    ),
    "cpp": (
        "m.cpp",
        """class A {
public:
    int run() { return 1; /* mk_run1 */ }
    int run(int x) { return x; /* mk_run2 */ }
    int step() { return 2; /* mk_step */ }
};
int top() { return 0; /* mk_top */ }
""",
    ),
    "csharp": (
        "A.cs",
        """class A {
    void Run() { System.Console.WriteLine("mk_run1"); }
    void Run(int x) { System.Console.WriteLine("mk_run2"); }
    void Step() { System.Console.WriteLine("mk_step"); }
}
""",
    ),
    "php": (
        "m.php",
        """<?php
class A {
    function run() { echo "mk_run"; }
    function step() { echo "mk_step"; }
}
function top() { echo "mk_top"; }
""",
    ),
    "kotlin": (
        "m.kt",
        """class A {
    fun run() { println("mk_run1") }
    fun run(x: Int) { println("mk_run2") }
    fun step() { println("mk_step") }
}
fun top() { println("mk_top") }
""",
    ),
    "swift": (
        "m.swift",
        """class A {
    func run() { print("mk_run1") }
    func run(x: Int) { print("mk_run2") }
    func step() { print("mk_step") }
}
func top() { print("mk_top") }
""",
    ),
    "scala": (
        "m.scala",
        """class A {
  def run(): Unit = println("mk_run1")
  def run(x: Int): Unit = println("mk_run2")
  def step(): Unit = println("mk_step")
}
object O {
  def top(): Unit = println("mk_top")
}
""",
    ),
    "bash": (
        "m.sh",
        """step() {
  echo mk_step
}
top() {
  echo mk_top1
}
top() {
  echo mk_top2
}
""",
    ),
    "lua": (
        "m.lua",
        """local A = {}
function A.run()
  print("mk_run")
end
function step()
  print("mk_step")
end
function top()
  print("mk_top")
end
""",
    ),
    "objc": (
        "m.m",
        """@implementation A
- (void)run {
    puts("mk_run1");
}
- (void)run {
    puts("mk_run2");
}
- (void)step {
    puts("mk_step");
}
@end
void top(void) {
    puts("mk_top");
}
""",
    ),
    "hcl": (
        "m.tf",
        """resource "a" "run" {
  name = "mk_run1"
}
resource "a" "run" {
  name = "mk_run2"
}
variable "step" {
  default = "mk_step"
}
locals {
  top = "mk_top"
}
""",
    ),
}


# Hand-derived: id suffix after `sym:<file>::` -> the markers its own chunk
# body contains. A leaf definition's chunk holds its own marker and no other
# leaf's; a container (class/impl/object) holds its members' markers.
EXPECTED: dict[str, dict[str, set[str]]] = {
    "python": {
        "A": {"mk_run1", "mk_step", "mk_run2"},
        "A.run": {"mk_run1"},
        "A.step": {"mk_step"},
        "A.run@L8": {"mk_run2"},
        "top": {"mk_top"},
    },
    "javascript": {
        "A": {"mk_run", "mk_step"},
        "A.run": {"mk_run"},
        "A.step": {"mk_step"},
        "top": {"mk_top1"},
        "top@L6": {"mk_top2"},
    },
    # Overload *signatures* (no body) are not definitions: only the
    # implementation is indexed, so there is nothing to disambiguate.
    "typescript": {
        "A": {"mk_run", "mk_step"},
        "A.run": {"mk_run"},
        "A.step": {"mk_step"},
        "top": {"mk_top"},
        "other": {"mk_other1"},
    },
    "tsx": {
        "A": {"mk_run", "mk_step"},
        "A.run": {"mk_run"},
        "A.step": {"mk_step"},
        "top": {"mk_top1"},
        "top@L6": {"mk_top2"},
    },
    "go": {
        "A": set(),
        "B": set(),
        "A.Run": {"mk_arun"},
        "A.step": {"mk_astep"},
        "B.Run": {"mk_brun"},
        "Top": {"mk_top"},
    },
    # `struct A` and `impl A` share the qualname `A`; the impl is the later one.
    "rust": {
        "A": set(),
        "A@L2": {"mk_run", "mk_step"},
        "A.run": {"mk_run"},
        "A.step": {"mk_step"},
        "top": {"mk_top"},
    },
    "java": {
        "A": {"mk_run1", "mk_run2", "mk_step"},
        "A.run": {"mk_run1"},
        "A.run@L3": {"mk_run2"},
        "A.step": {"mk_step"},
    },
    "ruby": {
        "A": {"mk_run1", "mk_step", "mk_run2"},
        "A.run": {"mk_run1"},
        "A.step": {"mk_step"},
        "A.run@L8": {"mk_run2"},
        "top": {"mk_top"},
    },
    "c": {"A": set(), "step": {"mk_step"}, "top": {"mk_top"}},
    "cpp": {
        "A": {"mk_run1", "mk_run2", "mk_step"},
        "A.run": {"mk_run1"},
        "A.run@L4": {"mk_run2"},
        "A.step": {"mk_step"},
        "top": {"mk_top"},
    },
    "csharp": {
        "A": {"mk_run1", "mk_run2", "mk_step"},
        "A.Run": {"mk_run1"},
        "A.Run@L3": {"mk_run2"},
        "A.Step": {"mk_step"},
    },
    "php": {
        "A": {"mk_run", "mk_step"},
        "A.run": {"mk_run"},
        "A.step": {"mk_step"},
        "top": {"mk_top"},
    },
    "kotlin": {
        "A": {"mk_run1", "mk_run2", "mk_step"},
        "A.run": {"mk_run1"},
        "A.run@L3": {"mk_run2"},
        "A.step": {"mk_step"},
        "top": {"mk_top"},
    },
    "swift": {
        "A": {"mk_run1", "mk_run2", "mk_step"},
        "A.run": {"mk_run1"},
        "A.run@L3": {"mk_run2"},
        "A.step": {"mk_step"},
        "top": {"mk_top"},
    },
    "scala": {
        "A": {"mk_run1", "mk_run2", "mk_step"},
        "A.run": {"mk_run1"},
        "A.run@L3": {"mk_run2"},
        "A.step": {"mk_step"},
        "O": {"mk_top"},
        "O.top": {"mk_top"},
    },
    "bash": {"step": {"mk_step"}, "top": {"mk_top1"}, "top@L7": {"mk_top2"}},
    "lua": {"A.run": {"mk_run"}, "step": {"mk_step"}, "top": {"mk_top"}},
    "objc": {
        "A": {"mk_run1", "mk_run2", "mk_step"},
        "A.run": {"mk_run1"},
        "A.run@L5": {"mk_run2"},
        "A.step": {"mk_step"},
        "top": {"mk_top"},
    },
    "hcl": {
        "a.run": {"mk_run1"},
        "a.run@L4": {"mk_run2"},
        "var.step": {"mk_step"},
        "local.top": {"mk_top"},
    },
}


def test_every_lang_cfg_language_has_a_fixture():
    assert set(FIXTURES) == set(LANG_CFG) == set(EXPECTED)


@pytest.mark.parametrize("lang", sorted(FIXTURES))
def test_every_definition_has_its_own_node_and_chunk(tmp_path: Path, lang: str):
    fname, src = FIXTURES[lang]
    (tmp_path / fname).write_text(src, encoding="utf8", newline="\n")
    pf = parse_source(src.encode(), lang)
    assert pf.parse_errors == 0
    g = build(tmp_path)
    prefix = f"sym:{fname}::"
    ids = [n for n, v in g.nodes.items() if v["type"] == "symbol"]
    # exactly one node per parsed definition, with the hand-derived ids
    assert len(ids) == len(pf.symbols)
    assert set(ids) == {prefix + k for k in EXPECTED[lang]}
    chunks = {c["id"]: c for c in build_chunks(g) if c["type"] == "symbol"}
    for key, markers in EXPECTED[lang].items():
        text = chunks[prefix + key]["text"]
        body = "\n".join(ln for ln in text.split("\n") if not ln.startswith("# "))
        assert set(re.findall(r"mk_\w+", body)) == markers, key


def test_go_methods_are_receiver_qualified_and_resolve_same_class(tmp_path: Path):
    (tmp_path / "m.go").write_text(FIXTURES["go"][1], encoding="utf8")
    # a method of A in another file of the same package is still same_class
    (tmp_path / "n.go").write_text(
        "package m\n\nfunc (a *A) Other() { a.step() }\n", encoding="utf8"
    )
    g = build(tmp_path)
    calls = {(e["src"], e["dst"]): e for e in g.edges if e["type"] == "CALLS"}
    for src in ("sym:m.go::A.Run", "sym:n.go::A.Other"):
        e = calls[(src, "sym:m.go::A.step")]
        assert (e["resolution_kind"], e["confidence"]) == ("same_class", 1.0)
    assert g.nodes["sym:m.go::B.Run"]["start_line"] == 8
    pf = parse_source(b"package m\nfunc (l *List[T]) Len() int { return 0 }\n", "go")
    assert [s.qualname for s in pf.symbols] == ["List.Len"]


def test_kotlin_functions_are_symbols_with_their_own_calls(tmp_path: Path):
    src = (
        "class A {\n    fun run() = helper()\n    companion object {\n"
        "        fun make(): A = A()\n    }\n}\n"
        "interface I {\n    fun x()\n}\n"
        "object O {\n    fun go() { helper() }\n}\n"
        "fun String.ext(): Int = length\n"
        "fun helper() {}\n"
    )
    pf = parse_source(src.encode(), "kotlin")
    got = {s.qualname: (s.kind, s.calls) for s in pf.symbols}
    assert got == {
        "A": ("class", []),
        "A.run": ("function", ["helper"]),
        # the companion is transparent: `A.make()` is how Kotlin calls it
        "A.make": ("function", ["A"]),
        "I": ("interface", []),
        "I.x": ("function", []),
        "O": ("object", []),
        "O.go": ("function", ["helper"]),
        "String.ext": ("function", []),
        "helper": ("function", []),
    }
    (tmp_path / "m.kt").write_text(src, encoding="utf8")
    g = build(tmp_path)
    calls = {(e["src"], e["dst"]) for e in g.edges if e["type"] == "CALLS"}
    assert ("sym:m.kt::A.run", "sym:m.kt::helper") in calls
    assert not any(s == "sym:m.kt::A" for s, _ in calls)


def test_overloads_fan_out_and_are_all_name_resolvable(tmp_path: Path):
    (tmp_path / "A.java").write_text(
        "class A {\n    void run() {}\n    void run(int x) {}\n    void step() { run(1); }\n}\n",
        encoding="utf8",
    )
    g = build(tmp_path)
    out = {
        e["dst"]: e for e in g.edges if e["type"] == "CALLS" and e["src"] == "sym:A.java::A.step"
    }
    assert set(out) == {"sym:A.java::A.run", "sym:A.java::A.run@L3"}
    assert {(e["resolution_kind"], e["confidence"]) for e in out.values()} == {("same_class", 0.5)}
    defines = {e["dst"] for e in g.edges if e["type"] == "DEFINES" and e["src"] == "sym:A.java::A"}
    assert {"sym:A.java::A.run", "sym:A.java::A.run@L3"} <= defines


def test_duplicate_keys_are_deterministic_and_survive_the_parse_cache():
    src = FIXTURES["python"][1].encode()
    a, b = parse_source(src, "python"), parse_source(src, "python")
    assert [s.key for s in a.symbols] == [s.key for s in b.symbols] == ["", "", "", "A.run@L8", ""]
    restored = entry_read(cache_entry("python", len(src), 14, a, "x"))
    assert restored is not None and restored[2] == a


def test_child_of_a_duplicate_definition_points_at_its_own_parent(tmp_path: Path):
    src = (
        "if X:\n    class C:\n        def m(self): pass\n"
        "else:\n    class C:\n        def m(self): pass\n"
    )
    (tmp_path / "m.py").write_text(src, encoding="utf8")
    g = build(tmp_path)
    defines = {(e["src"], e["dst"]) for e in g.edges if e["type"] == "DEFINES"}
    assert ("sym:m.py::C", "sym:m.py::C.m") in defines
    assert ("sym:m.py::C@L5", "sym:m.py::C.m@L6") in defines


def test_kotlin_secondary_constructors_and_accessors(tmp_path: Path):
    src = (
        "class User {\n"
        "    constructor(name: String) {}\n"
        "    var age: Int\n"
        "        get() = 42\n"
        "        set(value) {}\n"
        "}\n"
    )
    (tmp_path / "User.kt").write_text(src, encoding="utf8")
    g = build(tmp_path)
    symbols = {n["id"]: n["name"] for n in g.nodes.values()}
    assert "sym:User.kt::User.constructor" in symbols
    assert "sym:User.kt::User.get" in symbols
    assert "sym:User.kt::User.set" in symbols
