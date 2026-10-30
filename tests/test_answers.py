"""The answers of Garmin's servers kept for a while (answers.Answers): a fresh one is not asked
again, two asking at once share one request, a failure is never kept, what may have changed
is forgotten, and on disk only the fields the program reads, in a file only its owner reads.
All answers and unit IDs are made up."""
import json
import stat
import threading

import pytest

from openexpress import answers
from openexpress.answers import ACCOUNT_DEVICES, SOFTWARE_UPDATES, UNIT_INFO, Answers, Disk

UNIT = 3000000001


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def kept():
    clock = Clock()
    return Answers(clock), clock


class Server:
    """Counts what is asked; answers with a new list each time."""

    def __init__(self):
        self.asked = 0

    def __call__(self):
        self.asked += 1
        return [{"unitid": UNIT, "n": self.asked}]


def test_a_fresh_answer_is_not_asked_again(kept):
    store, clock = kept
    server = Server()
    first = store.ask(ACCOUNT_DEVICES, ("prod", "login"), server)
    clock.now += answers.LIFETIMES[ACCOUNT_DEVICES] - 1
    assert store.ask(ACCOUNT_DEVICES, ("prod", "login"), server) == first
    assert server.asked == 1
    clock.now += 2  # older than its lifetime
    assert store.ask(ACCOUNT_DEVICES, ("prod", "login"), server)[0]["n"] == 2


def test_another_key_is_another_answer(kept):
    store, _clock = kept
    server = Server()
    store.ask(ACCOUNT_DEVICES, ("prod", "login 1"), server)
    store.ask(ACCOUNT_DEVICES, ("prod", "login 2"), server)  # another login
    store.ask(ACCOUNT_DEVICES, ("cn", "login 1"), server)  # another environment
    assert server.asked == 3


def test_fresh_asks_again_and_keeps_the_new_answer(kept):
    store, _clock = kept
    server = Server()
    store.ask(ACCOUNT_DEVICES, ("k",), server)
    assert store.ask(ACCOUNT_DEVICES, ("k",), server, fresh=True)[0]["n"] == 2
    assert store.ask(ACCOUNT_DEVICES, ("k",), server)[0]["n"] == 2
    assert server.asked == 2


def test_each_caller_gets_a_copy_of_its_own(kept):
    store, _clock = kept
    store.ask(ACCOUNT_DEVICES, ("k",), Server())[0]["n"] = 99
    assert store.ask(ACCOUNT_DEVICES, ("k",), Server())[0]["n"] == 1


def test_two_asking_at_once_share_one_request(kept):
    store, _clock = kept
    started, release, asked, got = threading.Event(), threading.Event(), [], []

    def slow():
        asked.append(1)
        started.set()
        release.wait(5)
        return {"answer": len(asked)}

    first = threading.Thread(target=lambda: got.append(store.ask(SOFTWARE_UPDATES, ("x",), slow)))
    first.start()
    started.wait(5)
    second = threading.Thread(target=lambda: got.append(
        store.ask(SOFTWARE_UPDATES, ("x",), slow)))
    second.start()
    release.set()
    first.join(5)
    second.join(5)
    assert asked == [1] and got == [{"answer": 1}, {"answer": 1}]


def test_a_failure_is_not_kept_and_those_waiting_get_it(kept):
    store, _clock = kept
    started, release = threading.Event(), threading.Event()
    errors = []

    def failing():
        started.set()
        release.wait(5)
        raise OSError("refused")

    def ask():
        try:
            store.ask(SOFTWARE_UPDATES, ("x",), failing)
        except OSError as e:
            errors.append(str(e))

    threads = [threading.Thread(target=ask)]
    threads[0].start()
    started.wait(5)
    threads.append(threading.Thread(target=ask))
    threads[1].start()
    release.set()
    for t in threads:
        t.join(5)
    assert errors == ["refused", "refused"]
    assert store.ask(SOFTWARE_UPDATES, ("x",), lambda: {"ok": True}) == {"ok": True}


def test_forget_by_kind_and_by_what_it_is_about(kept):
    store, _clock = kept
    server = Server()
    store.ask(ACCOUNT_DEVICES, ("k",), server)
    store.ask(SOFTWARE_UPDATES, ("one",), server, about=[UNIT, "xml-1"])
    store.ask(SOFTWARE_UPDATES, ("two",), server, about=[3000000002, "xml-2"])
    store.forget(SOFTWARE_UPDATES, about=UNIT)
    store.ask(SOFTWARE_UPDATES, ("one",), server, about=[UNIT])  # asked again
    store.ask(SOFTWARE_UPDATES, ("two",), server)  # still kept
    store.ask(ACCOUNT_DEVICES, ("k",), server)  # still kept
    assert server.asked == 4
    store.forget()
    store.ask(ACCOUNT_DEVICES, ("k",), server)
    assert server.asked == 5


def test_unit_info_keeps_only_the_fields_the_program_reads(kept):
    store, _clock = kept
    answer = {"unitid": UNIT, "serialnumber": "SER123", "mapupdatetype": "UniversalDetect",
              "graphicurl": "https://res.garmin.com/x.png", "segmenttype": "Wearables",
              "devicemodel": "Made-up", "connectsupport": "Required", "other": {"a": 1}}
    got = store.ask(UNIT_INFO, ("k",), lambda: answer)
    assert got == {"mapupdatetype": "UniversalDetect", "graphicurl": "https://res.garmin.com/x.png",
                   "segmenttype": "Wearables", "devicemodel": "Made-up",
                   "connectsupport": "Required"}


def test_on_disk_only_the_fields_named_in_a_file_its_owner_reads(tmp_path, kept, monkeypatch):
    store, clock = kept
    monkeypatch.setattr(answers, "DISK_FIELDS",
                        {ACCOUNT_DEVICES: frozenset({"unitid", "partnumber"})})
    store.disk = Disk(tmp_path / "answers")
    answer = [{"unitid": UNIT, "partnumber": "006-B0000-00", "displayname": "Jan's watch"}]
    store.ask(ACCOUNT_DEVICES, ("prod", "login"), lambda: answer, about=[UNIT])
    (file,) = (tmp_path / "answers").iterdir()
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    text = file.read_text()
    assert "Jan's watch" not in text and "displayname" not in text
    assert json.loads(text)["tags"] and str(UNIT) not in json.dumps(json.loads(text)["tags"])
    # Another process: nothing in memory, the answer from disk while it is fresh.
    other = Answers(clock)
    other.disk = Disk(tmp_path / "answers")
    assert other.ask(ACCOUNT_DEVICES, ("prod", "login"), pytest.fail) == [
        {"unitid": UNIT, "partnumber": "006-B0000-00"}]
    clock.now += answers.LIFETIMES[ACCOUNT_DEVICES]
    other.clear()
    assert other.ask(ACCOUNT_DEVICES, ("prod", "login"), list) == []


def test_forget_takes_it_off_the_disk_too(tmp_path, kept, monkeypatch):
    store, _clock = kept
    monkeypatch.setattr(answers, "DISK_FIELDS", {SOFTWARE_UPDATES: frozenset({"updates"})})
    store.disk = Disk(tmp_path / "answers")
    store.ask(SOFTWARE_UPDATES, ("one",), lambda: {"updates": []}, about=[UNIT])
    store.ask(SOFTWARE_UPDATES, ("two",), lambda: {"updates": []}, about=[3000000002])
    store.forget(SOFTWARE_UPDATES, about=UNIT)
    assert len(list((tmp_path / "answers").iterdir())) == 1


def test_a_kind_not_named_for_the_disk_stays_in_memory(tmp_path, kept, monkeypatch):
    store, _clock = kept
    monkeypatch.setattr(answers, "DISK_FIELDS", {})
    store.disk = Disk(tmp_path / "answers")
    store.ask(ACCOUNT_DEVICES, ("k",), list)
    assert not (tmp_path / "answers").exists()


def test_every_kind_has_a_lifetime():
    kinds = [v for k, v in vars(answers).items() if k.isupper() and isinstance(v, str)
             and "-" in v]
    assert kinds and all(k in answers.LIFETIMES for k in kinds)


def test_the_tests_start_with_nothing_kept():
    assert answers.ANSWERS._kept == {} and answers.ANSWERS.disk is None


def test_an_answer_of_none_is_kept_too(kept, caplog):
    """No country on the account, an empty answer of the store: kept as any answer, and the
    log says "from memory" only when the answer kept is taken."""
    import logging

    store, clock = kept
    asked = []
    with caplog.at_level(logging.DEBUG, logger="openexpress.answers"):
        for _ in range(3):
            assert store.ask(answers.ACCOUNT_COUNTRY, ("k",), lambda: asked.append(1)) is None
        clock.now += answers.LIFETIMES[answers.ACCOUNT_COUNTRY] + 1
        store.ask(answers.ACCOUNT_COUNTRY, ("k",), lambda: asked.append(1))
    assert len(asked) == 2
    assert sum("from memory" in m for m in caplog.messages) == 2


@pytest.mark.parametrize("written", ["[]", "1", '"text"', "null"])
def test_a_file_on_disk_that_is_no_object_is_no_answer(tmp_path, kept, written):
    """A file of the cache changed by hand: taken for none, asked anew, written again."""
    store, _clock = kept
    store.disk = Disk(tmp_path)
    full = (SOFTWARE_UPDATES, "k")
    tmp_path.mkdir(exist_ok=True)
    store.disk._path(full).write_text(written)
    assert store.disk.get(SOFTWARE_UPDATES, full, 1_800_000_000.0) is None
    assert store.ask(SOFTWARE_UPDATES, ("k",), lambda: {"updates": []}) == {"updates": []}
    assert json.loads(store.disk._path(full).read_text())["kind"] == SOFTWARE_UPDATES


# --- a request under way and what changed meanwhile (generations) -----------------------------


class Slow:
    """A request that waits until let go; the answer is what was true when it was asked."""

    def __init__(self, answer):
        self.answer, self.started, self.go = answer, threading.Event(), threading.Event()

    def __call__(self):
        answer = self.answer
        self.started.set()
        assert self.go.wait(5)
        return answer


def _under_way(store, slow, **kw):
    out = []
    thread = threading.Thread(target=lambda: out.append(store.ask(SOFTWARE_UPDATES, ("k",),
                                                                  slow, **kw)))
    thread.start()
    assert slow.started.wait(5)
    return thread, out


def test_an_answer_asked_before_forget_is_not_kept(kept):
    store, _clock = kept
    slow = Slow({"n": "before"})
    thread, out = _under_way(store, slow)
    store.forget(SOFTWARE_UPDATES)  # what it says may have changed meanwhile
    slow.go.set()
    thread.join(5)
    assert out == [{"n": "before"}]  # the one who asked before gets it
    assert store.ask(SOFTWARE_UPDATES, ("k",), lambda: {"n": "after"}) == {"n": "after"}


def test_after_forget_a_question_does_not_join_the_request_begun_before(kept):
    store, _clock = kept
    slow = Slow({"n": "before"})
    thread, _out = _under_way(store, slow)
    store.forget(SOFTWARE_UPDATES)
    try:
        assert store.ask(SOFTWARE_UPDATES, ("k",), lambda: {"n": "after"}) == {"n": "after"}
    finally:
        slow.go.set()
        thread.join(5)
    assert store.ask(SOFTWARE_UPDATES, ("k",), lambda: {"n": "third"}) == {"n": "after"}


def test_fresh_does_not_join_a_request_begun_before(kept):
    """fresh: what the server says now; the older answer, coming later, takes nothing over."""
    store, _clock = kept
    slow = Slow({"n": "before"})
    thread, _out = _under_way(store, slow)
    try:
        assert store.ask(SOFTWARE_UPDATES, ("k",), lambda: {"n": "now"},
                         fresh=True) == {"n": "now"}
    finally:
        slow.go.set()
        thread.join(5)
    assert store.ask(SOFTWARE_UPDATES, ("k",), lambda: {"n": "third"}) == {"n": "now"}


def test_those_asking_after_forget_share_one_new_request(kept):
    store, _clock = kept
    slow = Slow({"n": "before"})
    thread, _out = _under_way(store, slow)
    store.forget(SOFTWARE_UPDATES)
    second = Slow({"n": "after"})
    other, got = _under_way(store, second)  # its own request, not the one begun before
    never, third = [], []
    late = threading.Thread(target=lambda: third.append(store.ask(
        SOFTWARE_UPDATES, ("k",), lambda: never.append(1) or {"n": "never"})))
    late.start()
    second.go.set()
    slow.go.set()
    for t in (thread, other, late):
        t.join(5)
    assert got == third == [{"n": "after"}] and never == []
