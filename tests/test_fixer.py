import pytest

from flakehunt import fixer


def block(path, search, repl):
    return "<<<<<<< FILE %s\n<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> END\n" % (path, search, repl)


def test_parse_edits():
    edits = fixer.parse_edits("intro\n" + block("conftest.py", "old", "new") + "outro")
    assert edits == [{"path": "conftest.py", "search": "old", "replace": "new"}]


def test_parse_empty_search_means_append():
    text = "<<<<<<< FILE a.py\n<<<<<<< SEARCH\n=======\nx = 1\n>>>>>>> END\n"
    assert fixer.parse_edits(text)[0]["search"] == ""


def test_apply_replaces_unique_text(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    changes = fixer.apply_edits(str(tmp_path), [{"path": "a.py", "search": "y = 2", "replace": "y = 3"}])
    assert changes["a.py"][1] == "x = 1\ny = 3\n"


def test_apply_rejects_ambiguous_or_missing_search(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")
    with pytest.raises(fixer.EditError):
        fixer.apply_edits(str(tmp_path), [{"path": "a.py", "search": "x = 1", "replace": "z"}])
    with pytest.raises(fixer.EditError):
        fixer.apply_edits(str(tmp_path), [{"path": "a.py", "search": "nope", "replace": "z"}])


def test_apply_rejects_path_traversal(tmp_path):
    with pytest.raises(fixer.EditError):
        fixer.apply_edits(str(tmp_path), [{"path": "../evil.py", "search": "", "replace": "x"}])
    with pytest.raises(fixer.EditError):
        fixer.apply_edits(str(tmp_path), [{"path": "/etc/passwd", "search": "", "replace": "x"}])


def test_append_creates_missing_file(tmp_path):
    changes = fixer.apply_edits(str(tmp_path), [{"path": "new/conftest.py", "search": "", "replace": "import pytest"}])
    assert changes["new/conftest.py"][0] is None
    fixer.write_changes(str(tmp_path), changes)
    assert (tmp_path / "new" / "conftest.py").read_text() == "import pytest\n"


def test_guard_blocks_removed_assert(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_a():\n    assert 1 == 1\n")
    changes = fixer.apply_edits(str(tmp_path), [{"path": "test_x.py", "search": "assert 1 == 1", "replace": "pass"}])
    with pytest.raises(fixer.EditError):
        fixer.check_guards(changes)


def test_diff_has_unified_format(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    changes = fixer.apply_edits(str(tmp_path), [{"path": "a.py", "search": "x = 1", "replace": "x = 2"}])
    diff = fixer.make_diff(changes)
    assert "--- a/a.py" in diff and "+x = 2" in diff
