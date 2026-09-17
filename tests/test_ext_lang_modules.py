"""Coverage for TypeScript ESM/CJS module extensions (.mts / .cts)."""
from repo2graph.parse import EXT_LANG, parse_source


def test_ext_lang_maps_typescript_module_extensions():
    """Node ESM/CJS TypeScript extensions must parse as typescript, not fall through."""
    assert EXT_LANG[".mts"] == "typescript"
    assert EXT_LANG[".cts"] == "typescript"
    src = b"export function ping(): string { return \"ok\"; }\n"
    for ext in (".mts", ".cts"):
        pf = parse_source(src, EXT_LANG[ext])
        names = {s.name for s in pf.symbols}
        assert "ping" in names, (ext, names)
