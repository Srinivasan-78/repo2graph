# Repo map: ripgrep

files: 229  nodes: 4857  edges: 20381
languages: rust=110, md=23, toml=14, csv=12, yml=5, lock=2, setup=2, bash=2, help=2, gitignore=2, lua=1

## Most depended-on files
- crates/printer/src/path.rs (in=9)
- crates/cli/src/process.rs (in=3)
- crates/ignore/src/gitignore.rs (in=2)
- crates/pcre2/src/error.rs (in=2)
- crates/index/src/index.rs (in=1)
- crates/index/src/literal.rs (in=1)
- crates/printer/src/hyperlink/aliases.rs (in=1)
- crates/regex/src/ast.rs (in=1)
- crates/searcher/src/searcher/mmap.rs (in=1)

## Most called symbols
- crates/index/src/literal.rs::GramQuery.unwrap (function, in=374)
- crates/core/flags/parse.rs::parse_low_raw (function, in=108)
- crates/printer/src/util.rs::PrinterPath<'a>.as_bytes (function, in=91)
- crates/printer/src/standard.rs::StandardImpl<'a, M, W>.new (function, in=90)
- crates/printer/src/util.rs::DecimalFormatter.as_bytes (function, in=90)
- crates/printer/src/standard.rs::StandardBuilder.new (function, in=88)
- crates/printer/src/standard.rs::PreludeWriter<'a, M, W>.new (function, in=84)
- crates/printer/src/standard.rs::Standard<W>.new (function, in=82)
- crates/ignore/src/lib.rs::Error.clone (function, in=81)
- crates/printer/src/standard.rs::tests.line_number (function, in=80)
- crates/globset/src/glob.rs::Glob.as_ref (function, in=77)
- crates/printer/src/standard.rs::StandardBuilder.build (function, in=76)
- crates/printer/src/standard.rs::tests.printer_contents (function, in=71)
- crates/printer/src/standard.rs::Standard<W>.sink (function, in=69)
- crates/printer/src/standard.rs::StandardImpl<'a, M, W>.sink (function, in=68)
- crates/searcher/src/searcher/glue.rs::MultiLine<'s, M, S>.new (function, in=41)
- crates/ignore/src/walk.rs::WalkBuilder.new (function, in=40)
- crates/searcher/src/searcher/glue.rs::SliceByLine<'s, M, S>.new (function, in=40)
- crates/core/flags/mod.rs::FlagValue.unwrap_switch (function, in=39)
- crates/searcher/src/searcher/glue.rs::ReadByLine<'s, M, R, S>.new (function, in=39)
- crates/ignore/src/walk.rs::DirEntryRaw.path (function, in=38)
- crates/ignore/src/dir.rs::IgnoreBuilder.new (function, in=35)
- crates/ignore/src/walk.rs::DirEntryInner.path (function, in=35)
- crates/ignore/src/walk.rs::DirEntry.path (function, in=34)
- crates/core/flags/mod.rs::FlagValue.unwrap_value (function, in=33)