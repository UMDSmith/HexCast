from hexcast_core import requirements as rq


def test_parse_reads_names_extras_and_versions():
    reqs = rq.parse("""
# a comment
httpx>=0.27            # trailing comment
uvicorn[standard]>=0.27
websockets >= 12.0 , < 99
yt-dlp
""")
    assert [r.name for r in reqs] == ["httpx", "uvicorn", "websockets", "yt-dlp"]
    assert reqs[0].specs == ((">=", "0.27"),) and reqs[0].sure
    assert reqs[2].specs == ((">=", "12.0"), ("<", "99"))
    assert reqs[3].specs == () and reqs[3].sure


def test_anything_unusual_is_left_to_pip():
    reqs = rq.parse("-r other.txt\nfoo ; python_version < '3.11'\nbar~=1.4\nbaz @ https://example.com/baz.zip\n")
    assert [r.sure for r in reqs] == [False, False, False, False]
    assert not any(rq.satisfied(r) for r in reqs)


def test_satisfied_compares_installed_versions():
    ok = lambda line: rq.satisfied(rq.parse(line)[0])
    assert ok("pytest>=1.0")
    assert ok("pytest")
    assert not ok("pytest>=999")
    assert not ok("pytest<1")
    assert not ok("definitely-not-installed-xyz>=1")


def test_version_compare_ignores_trailing_zeros():
    assert rq._cmp((1, 7), (1, 7, 0)) == 0
    assert rq._cmp((2025, 11, 12), (2025, 9, 1)) == 1
    assert rq._cmp((0, 27), (0, 110)) == -1


def test_lock_remembers_a_check_and_notices_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "_lock_dir", lambda: tmp_path / "locks")
    req = tmp_path / "requirements.txt"
    req.write_text("pytest>=1\n")
    assert rq.deps_ok("demo", req)
    assert (tmp_path / "locks" / "demo").exists()
    req.write_text("pytest>=1\ndefinitely-not-installed-xyz>=1\n")           # edited: the lock no longer matches
    assert not rq.deps_ok("demo", req)
    rq.mark_installed("demo", req.read_text())                                # as after a successful pip run
    assert rq.deps_ok("demo", req)
    rq.forget("demo")
    assert not rq.deps_ok("demo", req)


def test_no_requirements_file_is_fine(tmp_path):
    assert rq.deps_ok("demo", None)
    assert rq.deps_ok("demo", tmp_path / "missing.txt")
