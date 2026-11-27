pub mod core;

use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::collections::{HashMap, HashSet};
use std::path::Path;

#[pyfunction]
fn inspect_files(
    py: Python<'_>,
    paths: Vec<String>,
    max_file_bytes: u64,
    chunk_large_files: bool,
    read_max_bytes: usize,
) -> Vec<(u8, u64, usize, String, Option<Py<PyBytes>>)> {
    let results = py.detach(|| {
        core::inspect_files_batch(&paths, max_file_bytes, chunk_large_files, read_max_bytes)
    });
    results
        .into_iter()
        .map(|r| {
            let py_bytes = r.bytes.map(|b| PyBytes::new(py, &b).unbind());
            (r.status, r.size, r.lines, r.sha256, py_bytes)
        })
        .collect()
}

#[pyfunction]
fn hash_files(py: Python<'_>, paths: Vec<String>) -> Vec<Option<(u64, usize, String)>> {
    py.detach(|| core::hash_files_batch(&paths))
}

#[pyfunction]
fn read_files_bytes(py: Python<'_>, paths: Vec<String>) -> Vec<Option<Py<PyBytes>>> {
    let raw = py.detach(|| core::read_files_bytes_batch(&paths));
    raw.into_iter()
        .map(|opt| opt.map(|b| PyBytes::new(py, &b).unbind()))
        .collect()
}

#[pyfunction]
fn walk_files(py: Python<'_>, root: String, skip_dirs: Vec<String>) -> Vec<String> {
    let skip: HashSet<String> = skip_dirs.into_iter().collect();
    py.detach(|| core::walk_files(Path::new(&root), &skip))
}

#[pyfunction]
fn tokenize(text: &str) -> Vec<String> {
    core::tokenize_str(text)
}

#[pyfunction]
fn build_bm25(
    py: Python<'_>,
    texts: Vec<String>,
    qualnames: Vec<String>,
    names: Vec<String>,
    default_avg_len: f64,
) -> (
    HashMap<String, usize>,
    HashMap<String, Vec<(usize, usize)>>,
    Vec<usize>,
    f64,
) {
    let data =
        py.detach(|| core::build_bm25_data(&texts, &qualnames, &names, default_avg_len));
    (data.df, data.postings, data.lengths, data.avgdl)
}

#[pyfunction]
fn count_hidden_unicode(text: &str) -> usize {
    core::count_hidden_unicode_str(text)
}

#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(inspect_files, m)?)?;
    m.add_function(wrap_pyfunction!(hash_files, m)?)?;
    m.add_function(wrap_pyfunction!(read_files_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(walk_files, m)?)?;
    m.add_function(wrap_pyfunction!(tokenize, m)?)?;
    m.add_function(wrap_pyfunction!(build_bm25, m)?)?;
    m.add_function(wrap_pyfunction!(count_hidden_unicode, m)?)?;
    Ok(())
}
