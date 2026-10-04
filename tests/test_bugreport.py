"""Pairing tests between `repo2graph bug-report --category` and the GitHub issue templates.

`repo2graph/bugreport.py` promises that every feedback category is reachable from a
template, and the templates in turn send reporters at each other by name. Both halves
have silently broken before: the five per-category templates were deleted while
`bug_report.yml` and `.github/CONTRIBUTING.md` kept routing people to two of them, and
nothing in the suite noticed because no test read `.github/ISSUE_TEMPLATE/` at all.

These tests read the templates as data:
- every `CATEGORIES` key is offered by some template
- every template named in prose exists under its own `name:`
- every relative link out of a template resolves to a file on disk
"""

import re
from pathlib import Path

import yaml

from repo2graph.bugreport import CATEGORIES

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
CONTRIBUTING_PATH = REPO_ROOT / ".github" / "CONTRIBUTING.md"

# `config.yml` is the chooser's link list, not a form -- it has no `name:`/`body:`.
_NOT_A_FORM = {"config.yml"}


def _forms() -> dict[Path, dict]:
    """Every issue *form* under .github/ISSUE_TEMPLATE/, parsed, keyed by path."""
    out: dict[Path, dict] = {}
    for path in sorted(TEMPLATE_DIR.glob("*.yml")):
        if path.name in _NOT_A_FORM:
            continue
        out[path] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return out


def _dropdown_options(form: dict) -> list[str]:
    """Flattened option strings from every dropdown in a form."""
    return [
        str(option)
        for field in form.get("body", [])
        if field.get("type") == "dropdown"
        for option in field.get("attributes", {}).get("options", [])
    ]


def test_every_feedback_category_is_offered_by_some_template():
    """A `--category` value with no route into an issue is a dead end: the CLI tells
    the user to file one, and the chooser has nowhere for it to go."""
    offered = " ".join(option for form in _forms().values() for option in _dropdown_options(form))
    missing = [category for category in CATEGORIES if category not in offered]
    assert not missing, (
        f"bug-report categories with no issue template offering them: {missing}. "
        f"Add them to a dropdown in .github/ISSUE_TEMPLATE/, or drop them from "
        f"repo2graph.bugreport.CATEGORIES."
    )


def test_templates_named_in_prose_exist():
    """`bug_report.yml` and CONTRIBUTING.md send reporters to other templates by their
    display name. Deleting a template leaves those instructions pointing at nothing."""
    names = {form.get("name") for form in _forms().values()}

    prose = CONTRIBUTING_PATH.read_text(encoding="utf-8")
    for path, form in _forms().items():
        prose += "\n" + path.read_text(encoding="utf-8")

    for referenced in ("Incorrect or missing graph edge", "Language / parser support"):
        assert referenced in prose, (
            f"'{referenced}' is no longer referenced anywhere -- if the template was "
            f"retired on purpose, drop it from this list too."
        )
        assert referenced in names, (
            f"prose routes reporters to the '{referenced}' template, but no form in "
            f".github/ISSUE_TEMPLATE/ declares that name. Either restore the template "
            f"or rewrite every reference to it."
        )


def test_relative_links_in_templates_resolve():
    """Templates sit two directories below the repo root, so `../../docs/x.md` is the
    only shape that works. An extra `../` renders as a 404 on github.com and nothing
    else in the suite reads these files."""
    link_re = re.compile(r"\]\((\.\./[^)#]+)(?:#[^)]*)?\)")
    broken: list[str] = []

    for path in sorted(TEMPLATE_DIR.glob("*.yml")):
        for target in link_re.findall(path.read_text(encoding="utf-8")):
            if not (TEMPLATE_DIR / target).resolve().is_file():
                broken.append(f"{path.name}: {target}")

    assert not broken, f"issue templates link to files that do not exist: {broken}"
