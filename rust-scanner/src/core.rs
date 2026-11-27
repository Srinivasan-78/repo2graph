use ignore::WalkBuilder;
use rayon::prelude::*;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct ScannedFile {
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Clone)]
pub struct InspectedFile {
    /// 0 = ok, 1 = unreadable, 2 = not_regular_file, 3 = too_large, 4 = lfs_pointer, 5 = binary
    pub status: u8,
    pub size: u64,
    pub lines: usize,
    pub sha256: String,
    pub bytes: Option<Vec<u8>>,
}

const LFS_LF: &[u8] = b"version https://git-lfs.github.com/spec/v1\n";
const LFS_CRLF: &[u8] = b"version https://git-lfs.github.com/spec/v1\r\n";

#[inline]
pub fn is_lfs_pointer(header: &[u8]) -> bool {
    header.starts_with(LFS_LF) || header.starts_with(LFS_CRLF)
}

fn is_valid_utf16_le(bytes: &[u8]) -> bool {
    if bytes.len() % 2 != 0 {
        return false;
    }
    let units: Vec<u16> = bytes
        .chunks_exact(2)
        .map(|c| u16::from_le_bytes([c[0], c[1]]))
        .collect();
    String::from_utf16(&units).is_ok()
}

fn is_valid_utf16_be(bytes: &[u8]) -> bool {
    if bytes.len() % 2 != 0 {
        return false;
    }
    let units: Vec<u16> = bytes
        .chunks_exact(2)
        .map(|c| u16::from_be_bytes([c[0], c[1]]))
        .collect();
    String::from_utf16(&units).is_ok()
}

fn is_valid_utf32(bytes: &[u8], le: bool) -> bool {
    if bytes.len() % 4 != 0 {
        return false;
    }
    bytes.chunks_exact(4).all(|c| {
        let cp = if le {
            u32::from_le_bytes([c[0], c[1], c[2], c[3]])
        } else {
            u32::from_be_bytes([c[0], c[1], c[2], c[3]])
        };
        char::from_u32(cp).is_some()
    })
}

pub fn is_binary_header(header: &[u8]) -> bool {
    if header.is_empty() {
        return false;
    }
    let has_bom = header.starts_with(b"\xef\xbb\xbf")
        || header.starts_with(b"\xff\xfe\x00\x00")
        || header.starts_with(b"\x00\x00\xfe\xff")
        || header.starts_with(b"\xff\xfe")
        || header.starts_with(b"\xfe\xff");

    if has_bom {
        if header.starts_with(b"\xef\xbb\xbf") && std::str::from_utf8(&header[3..]).is_ok() {
            return false;
        }
        if header.starts_with(b"\xff\xfe\x00\x00") && is_valid_utf32(&header[4..], true) {
            return false;
        }
        if header.starts_with(b"\x00\x00\xfe\xff") && is_valid_utf32(&header[4..], false) {
            return false;
        }
        if header.starts_with(b"\xff\xfe") && is_valid_utf16_le(&header[2..]) {
            return false;
        }
        if header.starts_with(b"\xfe\xff") && is_valid_utf16_be(&header[2..]) {
            return false;
        }
    }
    header.contains(&0)
}

#[inline]
pub fn count_lines(data: &[u8]) -> usize {
    data.iter().filter(|&&b| b == b'\n').count() + 1
}

#[cfg(windows)]
fn to_long_path(p: &Path) -> PathBuf {
    let s = p.to_string_lossy();
    if s.len() >= 250 && !s.starts_with(r"\\?\") {
        if let Ok(abs) = fs::canonicalize(p) {
            return abs;
        }
    }
    p.to_path_buf()
}

#[cfg(not(windows))]
fn to_long_path(p: &Path) -> PathBuf {
    p.to_path_buf()
}

pub fn inspect_single_file(
    path_str: &str,
    max_file_bytes: u64,
    chunk_large_files: bool,
    read_max_bytes: usize,
) -> InspectedFile {
    let path = to_long_path(Path::new(path_str));
    let meta = match fs::symlink_metadata(&path) {
        Ok(m) => m,
        Err(_) => {
            return InspectedFile {
                status: 1,
                size: 0,
                lines: 0,
                sha256: String::new(),
                bytes: None,
            }
        }
    };
    if !meta.file_type().is_file() {
        return InspectedFile {
            status: 2,
            size: 0,
            lines: 0,
            sha256: String::new(),
            bytes: None,
        };
    }
    let size = meta.len();
    if size > max_file_bytes {
        if !chunk_large_files {
            return InspectedFile {
                status: 3,
                size,
                lines: 0,
                sha256: String::new(),
                bytes: None,
            };
        }
        // For chunked large files, only read header (up to 4096 bytes) for binary/LFS check.
        let mut fh = match fs::File::open(&path) {
            Ok(f) => f,
            Err(_) => {
                return InspectedFile {
                    status: 1,
                    size,
                    lines: 0,
                    sha256: String::new(),
                    bytes: None,
                }
            }
        };
        let mut hdr = vec![0u8; (size as usize).min(4096)];
        let n = fh.read(&mut hdr).unwrap_or(0);
        hdr.truncate(n);
        if size < 512 && is_lfs_pointer(&hdr) {
            return InspectedFile {
                status: 4,
                size,
                lines: 0,
                sha256: String::new(),
                bytes: None,
            };
        }
        if is_binary_header(&hdr) {
            return InspectedFile {
                status: 5,
                size,
                lines: 0,
                sha256: String::new(),
                bytes: None,
            };
        }
        return InspectedFile {
            status: 0,
            size,
            lines: 0,
            sha256: String::new(),
            bytes: None,
        };
    }

    let data = match fs::read(&path) {
        Ok(d) => d,
        Err(_) => {
            return InspectedFile {
                status: 1,
                size,
                lines: 0,
                sha256: String::new(),
                bytes: None,
            }
        }
    };

    if size < 512 && is_lfs_pointer(&data) {
        return InspectedFile {
            status: 4,
            size,
            lines: 0,
            sha256: String::new(),
            bytes: None,
        };
    }
    let hdr_len = data.len().min(4096);
    if is_binary_header(&data[..hdr_len]) {
        return InspectedFile {
            status: 5,
            size,
            lines: 0,
            sha256: String::new(),
            bytes: None,
        };
    }

    let actual_size = data.len() as u64;
    let lines = count_lines(&data);
    let sha256 = hex::encode(Sha256::digest(&data));
    let bytes = if data.len() <= read_max_bytes {
        Some(data)
    } else {
        None
    };

    InspectedFile {
        status: 0,
        size: actual_size,
        lines,
        sha256,
        bytes,
    }
}

pub fn inspect_files_batch(
    paths: &[String],
    max_file_bytes: u64,
    chunk_large_files: bool,
    read_max_bytes: usize,
) -> Vec<InspectedFile> {
    paths
        .par_iter()
        .map(|p| inspect_single_file(p, max_file_bytes, chunk_large_files, read_max_bytes))
        .collect()
}

pub fn hash_files_batch(paths: &[String]) -> Vec<Option<(u64, usize, String)>> {
    paths
        .par_iter()
        .map(|p| {
            let path = to_long_path(Path::new(p));
            let data = fs::read(&path).ok()?;
            let size = data.len() as u64;
            let lines = count_lines(&data);
            let sha = hex::encode(Sha256::digest(&data));
            Some((size, lines, sha))
        })
        .collect()
}

pub fn read_files_bytes_batch(paths: &[String]) -> Vec<Option<Vec<u8>>> {
    paths
        .par_iter()
        .map(|p| {
            let path = to_long_path(Path::new(p));
            fs::read(&path).ok()
        })
        .collect()
}

pub fn walk_files(root: &Path, skip_dirs: &HashSet<String>) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let skip = skip_dirs.clone();
    for entry in WalkBuilder::new(root)
        .hidden(false)
        .git_ignore(false)
        .git_global(false)
        .git_exclude(false)
        .filter_entry(move |e| {
            if e.depth() == 0 {
                return true;
            }
            if e.file_type().is_some_and(|ft| ft.is_dir()) {
                let name = e.file_name().to_string_lossy();
                if skip.contains(name.as_ref()) {
                    return false;
                }
                if e.path().join(".git").exists() {
                    return false;
                }
            }
            true
        })
        .build()
        .flatten()
    {
        if entry.file_type().is_some_and(|ft| ft.is_file()) {
            out.push(entry.into_path().to_string_lossy().into_owned());
        }
    }
    out
}

pub fn scan_workspace(root: &Path) -> Vec<ScannedFile> {
    let mut paths: Vec<PathBuf> = Vec::new();
    for entry in WalkBuilder::new(root)
        .hidden(false)
        .git_ignore(true)
        .git_global(false)
        .git_exclude(true)
        .filter_entry(|e| {
            e.file_name() != ".git" && e.file_name() != "target" && e.file_name() != "node_modules"
        })
        .build()
        .flatten()
    {
        if entry.file_type().is_some_and(|ft| ft.is_file()) {
            paths.push(entry.into_path());
        }
    }

    let mut results: Vec<ScannedFile> = paths
        .par_iter()
        .filter_map(|p| {
            let bytes = fs::metadata(p).ok()?.len();
            let data = fs::read(p).ok()?;
            let digest = Sha256::digest(&data);
            let rel = p
                .strip_prefix(root)
                .ok()?
                .to_string_lossy()
                .replace('\\', "/");
            Some(ScannedFile {
                path: rel,
                bytes,
                sha256: hex::encode(digest),
            })
        })
        .collect();

    results.sort_by(|a, b| a.path.cmp(&b.path));
    results
}

#[inline]
fn is_ident_start(b: u8) -> bool {
    b.is_ascii_alphabetic() || b == b'_'
}

#[inline]
fn is_ident_cont(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}

/// Exact parity with Python `repo2graph.query.tokenize(text)`.
pub fn tokenize_str(text: &str) -> Vec<String> {
    let bytes = text.as_bytes();
    let len = bytes.len();
    let mut out = Vec::new();
    let mut i = 0;

    while i < len {
        if is_ident_start(bytes[i]) {
            let start = i;
            i += 1;
            while i < len && is_ident_cont(bytes[i]) {
                i += 1;
            }
            if i - start >= 2 {
                let tok = &bytes[start..i];
                let low: String = tok.iter().map(|&b| (b as char).to_ascii_lowercase()).collect();
                out.push(low.clone());

                // Split on `_` or `(?<=[a-z0-9])(?=[A-Z])`
                let mut part_start = 0;
                for j in 0..tok.len() {
                    if tok[j] == b'_' {
                        if j - part_start > 2 {
                            let part: String = tok[part_start..j]
                                .iter()
                                .map(|&b| (b as char).to_ascii_lowercase())
                                .collect();
                            if part != low {
                                out.push(part);
                            }
                        }
                        part_start = j + 1;
                    } else if j > part_start
                        && tok[j].is_ascii_uppercase()
                        && (tok[j - 1].is_ascii_lowercase() || tok[j - 1].is_ascii_digit())
                    {
                        if j - part_start > 2 {
                            let part: String = tok[part_start..j]
                                .iter()
                                .map(|&b| (b as char).to_ascii_lowercase())
                                .collect();
                            if part != low {
                                out.push(part);
                            }
                        }
                        part_start = j;
                    }
                }
                if tok.len() - part_start > 2 {
                    let part: String = tok[part_start..]
                        .iter()
                        .map(|&b| (b as char).to_ascii_lowercase())
                        .collect();
                    if part != low {
                        out.push(part);
                    }
                }
            }
        } else {
            i += 1;
        }
    }
    out
}

pub struct Bm25IndexData {
    pub df: HashMap<String, usize>,
    pub postings: HashMap<String, Vec<(usize, usize)>>,
    pub lengths: Vec<usize>,
    pub avgdl: f64,
}

pub fn build_bm25_data(
    texts: &[String],
    qualnames: &[String],
    names: &[String],
    default_avg_len: f64,
) -> Bm25IndexData {
    let n = texts.len();
    let per_doc: Vec<(usize, HashMap<String, usize>)> = (0..n)
        .into_par_iter()
        .map(|i| {
            let mut counts: HashMap<String, usize> = HashMap::new();
            for term in tokenize_str(&texts[i]) {
                *counts.entry(term).or_insert(0) += 1;
            }
            for term in tokenize_str(&qualnames[i]) {
                *counts.entry(term).or_insert(0) += 3;
            }
            let name = &names[i];
            if name.len() == 1 {
                let b = name.as_bytes()[0];
                if is_ident_start(b) {
                    let low = (b as char).to_ascii_lowercase().to_string();
                    *counts.entry(low).or_insert(0) += 3;
                }
            }
            let doc_len: usize = counts.values().sum();
            (if doc_len == 0 { 1 } else { doc_len }, counts)
        })
        .collect();

    let mut df: HashMap<String, usize> = HashMap::new();
    let mut postings: HashMap<String, Vec<(usize, usize)>> = HashMap::new();
    let mut lengths: Vec<usize> = Vec::with_capacity(n);
    let mut total_len: usize = 0;

    for (i, (doc_len, counts)) in per_doc.into_iter().enumerate() {
        lengths.push(doc_len);
        total_len += doc_len;
        for (term, cnt) in counts {
            *df.entry(term.clone()).or_insert(0) += 1;
            postings.entry(term).or_default().push((i, cnt));
        }
    }

    let avgdl = if n > 0 {
        (total_len as f64) / (n as f64)
    } else {
        default_avg_len
    };

    Bm25IndexData {
        df,
        postings,
        lengths,
        avgdl,
    }
}

#[inline]
pub fn count_hidden_unicode_str(text: &str) -> usize {
    if text.is_ascii() {
        return 0;
    }
    text.chars()
        .filter(|&c| {
            matches!(
                c,
                '\u{061c}'
                    | '\u{200b}'..='\u{200f}'
                    | '\u{202a}'..='\u{202e}'
                    | '\u{2060}'..='\u{2064}'
                    | '\u{2066}'..='\u{2069}'
                    | '\u{feff}'
            )
        })
        .count()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[test]
    fn scans_files_with_sha256_and_sorted_paths() {
        let dir = env::temp_dir().join("r2g-rust-scan-test");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("src")).unwrap();
        fs::write(dir.join("src/a.py"), "print(1)\n").unwrap();
        fs::write(dir.join("README.md"), "# hi\n").unwrap();

        let files = scan_workspace(&dir);
        assert_eq!(files.len(), 2);
        assert_eq!(files[0].path, "README.md");
        assert_eq!(files[1].path, "src/a.py");
        assert_eq!(files[1].sha256.len(), 64);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn tokenize_matches_python_subtokens() {
        assert_eq!(
            tokenize_str("getUserById snake_case_fn a1B2"),
            vec![
                "getuserbyid",
                "get",
                "user",
                "snake_case_fn",
                "snake",
                "case",
                "a1b2"
            ]
        );
    }

    #[test]
    fn hidden_unicode_counts_correctly() {
        assert_eq!(count_hidden_unicode_str("hello world"), 0);
        assert_eq!(count_hidden_unicode_str("a\u{200b}b\u{feff}c"), 2);
    }
}
