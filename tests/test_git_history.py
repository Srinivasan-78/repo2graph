import os
import subprocess
from pathlib import Path
from repo2graph.graph import Graph
from repo2graph.graph import add_cochange


def test_add_cochange_edge_cases(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    subprocess.run(["git", "init", "--initial-branch=main"], cwd=repo, check=True)

    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"

    def run_git(*args):
        subprocess.run(["git"] + list(args), cwd=repo, env=env, check=True)

    def make_blob(content: str) -> str:
        return (
            subprocess.check_output(
                ["git", "hash-object", "-w", "--stdin"],
                input=content.encode("utf-8"),
                cwd=repo,
                env=env,
            )
            .decode()
            .strip()
        )

    def add_file(path: str, content: str):
        blob = make_blob(content)
        subprocess.run(
            [
                "git",
                "-c",
                "core.protectNTFS=false",
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{blob},{path}",
            ],
            cwd=repo,
            env=env,
            check=True,
        )

    # 1. Base files
    add_file("file.txt", "1")
    add_file("space file.txt", "1")
    run_git("commit", "-m", "Initial commit")

    # 2. Rename commit
    run_git("rm", "--cached", "file.txt")
    add_file("renamed.txt", "2")
    add_file("space file.txt", "2")
    run_git("commit", "-m", "Rename file.txt to renamed.txt, edit space file")

    # 3. Copy commit (We simulate copy by adding a new file with same content)
    add_file("copied.txt", "2")
    add_file("space file.txt", "3")
    run_git("commit", "-m", "Copy space file to copied, edit space file")

    # 4. Special characters
    add_file("file\twith\ttab.txt", "1")
    add_file("file\nwith\nnewline.txt", "1")
    add_file('file"with"quotes.txt', "1")
    add_file("café.txt", "1")
    run_git("commit", "-m", "Special characters")

    # 5. Edit special characters
    add_file("file\twith\ttab.txt", "2")
    add_file("file\nwith\nnewline.txt", "2")
    add_file('file"with"quotes.txt', "2")
    add_file("café.txt", "2")
    run_git("commit", "-m", "Edit special characters")

    # 6. Branch and merge
    run_git("checkout", "-b", "feature")
    add_file("branch_file.txt", "1")
    run_git("commit", "-m", "Feature commit")
    run_git("checkout", "main")

    # Explicitly remove special files to ensure consistent behavior across OSes
    # (since checkout might fail to create them on Windows but succeed on Linux)
    run_git(
        "rm",
        "--cached",
        "--ignore-unmatch",
        "file\twith\ttab.txt",
        "file\nwith\nnewline.txt",
        'file"with"quotes.txt',
    )

    add_file("main_file.txt", "1")
    run_git("commit", "-m", "Main commit")
    run_git("merge", "--no-ff", "feature", "-m", "Merge feature")

    # 7. Deleted file
    run_git("rm", "--cached", "renamed.txt")
    add_file("space file.txt", "4")
    run_git("commit", "-m", "Delete renamed.txt, edit space file")

    # 8. File outside filters
    add_file("outside.txt", "1")
    add_file("space file.txt", "5")
    run_git("commit", "-m", "Outside file and space file")

    g = Graph(repo, "test_repo")

    file_index = {
        "file.txt",
        "space file.txt",
        "renamed.txt",
        "copied.txt",
        "café.txt",
        "branch_file.txt",
        "main_file.txt",
        "file\twith\ttab.txt",
        "file\nwith\nnewline.txt",
        'file"with"quotes.txt',
        '"file\\twith\\ttab.txt"',
        '"file\\nwith\\nnewline.txt"',
        '"file\\"with\\"quotes.txt"',
    }

    add_cochange(g, repo, commits=20, file_index=file_index, min_pairs=1)

    edges = set()
    for data in g.edges:
        if data["type"] == "CO_CHANGE":
            edge = tuple(
                sorted([data["src"].replace("file:", ""), data["dst"].replace("file:", "")])
            )
            edges.add(edge)

    expected_edges = {
        ("file.txt", "space file.txt"),
        ("file.txt", "renamed.txt"),
        ("renamed.txt", "space file.txt"),
        ("copied.txt", "space file.txt"),
        ('"file\\nwith\\nnewline.txt"', '"file\\twith\\ttab.txt"'),
        ('"file\\"with\\"quotes.txt"', '"file\\nwith\\nnewline.txt"'),
        ('"file\\"with\\"quotes.txt"', '"file\\twith\\ttab.txt"'),
        ('"file\\nwith\\nnewline.txt"', "café.txt"),
        ('"file\\twith\\ttab.txt"', "café.txt"),
        ('"file\\"with\\"quotes.txt"', "café.txt"),
        ('"file\\nwith\\nnewline.txt"', "main_file.txt"),
        ('"file\\twith\\ttab.txt"', "main_file.txt"),
        ('"file\\"with\\"quotes.txt"', "main_file.txt"),
    }

    assert edges == expected_edges
