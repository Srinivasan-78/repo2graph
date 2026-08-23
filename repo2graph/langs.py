"""Per-language node-type configuration for symbol/call/import extraction."""

EXT_LANG = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".kt": "kotlin",
    ".scala": "scala",
    ".swift": "swift",
    ".sh": "bash", ".bash": "bash",
}

DOC_EXT = {".md", ".mdx", ".rst", ".txt", ".adoc"}
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}

# kind_map: tree-sitter node type -> logical symbol kind
# call_types: node types that represent a call site
# import_types: node types that represent an import/include
LANG_CFG = {
    "python": {
        "kind_map": {"function_definition": "function", "class_definition": "class"},
        "call_types": {"call"},
        "import_types": {"import_statement", "import_from_statement"},
        "doc": "python",
    },
    "javascript": {
        "kind_map": {
            "function_declaration": "function",
            "generator_function_declaration": "function",
            "method_definition": "method",
            "class_declaration": "class",
            "variable_declarator": "maybe_function",
        },
        "call_types": {"call_expression", "new_expression"},
        "import_types": {"import_statement", "export_statement"},
        "doc": "jsdoc",
    },
    "go": {
        "kind_map": {
            "function_declaration": "function",
            "method_declaration": "method",
            "type_spec": "type",
        },
        "call_types": {"call_expression"},
        "import_types": {"import_spec"},
        "doc": "line",
    },
    "rust": {
        "kind_map": {
            "function_item": "function", "struct_item": "struct", "enum_item": "enum",
            "trait_item": "trait", "impl_item": "impl", "mod_item": "module",
        },
        "call_types": {"call_expression", "macro_invocation"},
        "import_types": {"use_declaration"},
        "doc": "line",
    },
    "java": {
        "kind_map": {
            "method_declaration": "method", "constructor_declaration": "method",
            "class_declaration": "class", "interface_declaration": "interface",
            "enum_declaration": "enum",
        },
        "call_types": {"method_invocation", "object_creation_expression"},
        "import_types": {"import_declaration"},
        "doc": "jsdoc",
    },
    "ruby": {
        "kind_map": {"method": "method", "singleton_method": "method",
                     "class": "class", "module": "module"},
        "call_types": {"call"},
        "import_types": set(),
        "doc": "line",
    },
    "c": {
        "kind_map": {"function_definition": "function", "struct_specifier": "struct",
                     "enum_specifier": "enum"},
        "call_types": {"call_expression"},
        "import_types": {"preproc_include"},
        "doc": "line",
    },
    "csharp": {
        "kind_map": {"method_declaration": "method", "class_declaration": "class",
                     "interface_declaration": "interface", "struct_declaration": "struct"},
        "call_types": {"invocation_expression", "object_creation_expression"},
        "import_types": {"using_directive"},
        "doc": "line",
    },
    "php": {
        "kind_map": {"function_definition": "function", "method_declaration": "method",
                     "class_declaration": "class", "interface_declaration": "interface"},
        "call_types": {"function_call_expression", "member_call_expression", "object_creation_expression"},
        "import_types": {"namespace_use_declaration"},
        "doc": "jsdoc",
    },
    "kotlin": {
        "kind_map": {"function_declaration": "function", "class_declaration": "class",
                     "object_declaration": "object"},
        "call_types": {"call_expression"},
        "import_types": {"import_header"},
        "doc": "jsdoc",
    },
    "swift": {
        "kind_map": {"function_declaration": "function", "class_declaration": "class",
                     "protocol_declaration": "protocol"},
        "call_types": {"call_expression"},
        "import_types": {"import_declaration"},
        "doc": "line",
    },
    "scala": {
        "kind_map": {"function_definition": "function", "class_definition": "class",
                     "object_definition": "object", "trait_definition": "trait"},
        "call_types": {"call_expression"},
        "import_types": {"import_declaration"},
        "doc": "line",
    },
    "bash": {
        "kind_map": {"function_definition": "function"},
        "call_types": {"command"},
        "import_types": set(),
        "doc": "line",
    },
}
LANG_CFG["typescript"] = dict(LANG_CFG["javascript"])
LANG_CFG["typescript"]["kind_map"] = dict(
    LANG_CFG["javascript"]["kind_map"],
    interface_declaration="interface", type_alias_declaration="type",
    enum_declaration="enum", abstract_class_declaration="class",
)
LANG_CFG["tsx"] = LANG_CFG["typescript"]
LANG_CFG["cpp"] = dict(LANG_CFG["c"])
LANG_CFG["cpp"]["kind_map"] = dict(LANG_CFG["c"]["kind_map"],
                                   class_specifier="class", namespace_definition="namespace")
