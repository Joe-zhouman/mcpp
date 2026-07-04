import pytest
from mcpp.keypool import KeyPool


def test_round_robin_distributes():
    pool = KeyPool(["a", "b", "c"])
    assert pool.next() == "a"
    assert pool.next() == "b"
    assert pool.next() == "c"
    assert pool.next() == "a"


def test_pause_key_on_failure():
    pool = KeyPool(["a", "b"])
    assert pool.next() == "a"
    pool.mark_bad("a")
    assert pool.next() == "b"


def test_all_bad_raises():
    pool = KeyPool(["a"])
    pool.mark_bad("a")
    with pytest.raises(RuntimeError, match="No healthy keys"):
        pool.next()


def test_resume_key():
    pool = KeyPool(["a", "b"])
    pool.mark_bad("a")
    pool.mark_bad("b")
    pool.resume("a")
    assert pool.next() == "a"
