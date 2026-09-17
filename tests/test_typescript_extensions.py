"""TypeScript module extensions use the TypeScript parser."""
import pytest

from repo2graph.graph import parse_all


@pytest.mark.parametrize("extension", [".ts", ".mts", ".cts"])
def test_typescript_module_extensions(tmp_path, extension):
    path = tmp_path / ("example" + extension)
    path.write_text("export function greet(name: string): string { return name; }\n",
                    encoding="utf8")
    [(rel, lang, read)] = parse_all([(path.name, path)], jobs=1)
    assert rel == path.name
    assert lang == "typescript"
    assert read is not None
    parsed = read[2]
    assert parsed is not None
    assert parsed.parse_errors == 0
    assert [(symbol.name, symbol.kind) for symbol in parsed.symbols] == [("greet", "function")]
