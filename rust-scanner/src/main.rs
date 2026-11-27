#[allow(dead_code)]
mod core;

use core::scan_workspace;
use std::env;
use std::path::Path;

fn main() {
    let target = env::args().nth(1).unwrap_or_else(|| ".".to_string());
    let files = scan_workspace(Path::new(&target));
    println!(
        "{}",
        serde_json::to_string(&files).unwrap_or_else(|_| "[]".into())
    );
}
