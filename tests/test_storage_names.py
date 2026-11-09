"""How a storage finds a name on the device in any letter case. Looking a path up (resolve,
size, sizes) takes the name in its exact letter case first, else the first in any case as the
folder lists it; a write and the folders made for it take the first in any case, as listed.
A device may list two names that differ only in letter case, which no folder of a computer
holds, so the storage here is made up: its folders list their entries in the order given."""

import pytest

from openexpress.device.storage import DeviceError, Entry, Storage


class Listed(Storage):
    """Folders by their real path, each listing its entries in the order given; what is written
    and made is kept."""

    def __init__(self, folders):
        self.folders = {path: list(entries) for path, entries in folders.items()}
        self.written, self.made = {}, []

    def _list(self, real_path):
        if real_path not in self.folders:
            raise DeviceError(f"Cannot list {real_path}")
        return list(self.folders[real_path])

    def _read(self, real_path):
        raise DeviceError(f"Cannot read {real_path}")

    def _write(self, real_path, data):
        self.written[real_path] = data

    def _delete(self, real_path):
        raise DeviceError(f"Cannot delete {real_path}")

    def _mkdir(self, real_path):
        self.made.append(real_path)
        self.folders[real_path] = []


@pytest.fixture
def twins():
    """Two GARMIN folders and two activities that differ only in letter case, the exact ones
    listed second."""
    return Listed({
        "": [Entry("Garmin", True), Entry("GARMIN", True)],
        "Garmin": [Entry("A.fit", False, 7)],
        "GARMIN": [Entry("a.fit", False, 3), Entry("A.FIT", False, 5), Entry("Sub", True)],
    })


@pytest.mark.parametrize("path, real, size", [
    ("GARMIN/A.FIT", "GARMIN/A.FIT", 5),     # each name in its exact letter case
    ("GARMIN/a.fit", "GARMIN/a.fit", 3),
    ("garmin/a.fit", "Garmin/A.fit", 7),     # in no exact case: the first listed in any
    ("GARMIN/a.FIT", "GARMIN/a.fit", 3),
    ("GARMIN/sub", "GARMIN/Sub", None),      # a folder has no size
    ("GARMIN/none.fit", None, None),
    ("Other/A.FIT", None, None),
])
def test_a_path_is_looked_up_by_its_exact_name_first(twins, path, real, size):
    assert twins.resolve(path) == real
    assert twins.exists(path) is (real is not None)
    assert twins.size(path) == size
    assert twins.sizes([path]) == {path: size}


def test_sizes_answer_each_path_as_size_does(twins):
    paths = ["GARMIN/A.FIT", "garmin/a.fit", "GARMIN/a.fit", "GARMIN/none.fit", "GARMIN"]
    assert twins.sizes(paths) == {path: twins.size(path) for path in paths}
    assert twins.size("") is None and twins.sizes([""]) == {"": None}


def test_a_write_takes_the_first_name_listed_in_any_letter_case(twins):
    """The exact names are listed second: the first in any case is taken all the same."""
    assert twins.write("GARMIN/A.FIT", b"1") == "Garmin/A.fit"
    assert twins.write("GARMIN/a.fit", b"2") == "Garmin/A.fit"
    assert twins.write("garmin/new.fit", b"3") == "Garmin/new.fit"
    assert twins.written == {"Garmin/A.fit": b"2", "Garmin/new.fit": b"3"}
    assert twins.made == []


def test_a_folder_is_made_only_where_none_has_its_name_in_any_letter_case(twins):
    assert twins.make_dir("garmin") == "Garmin"
    assert twins.make_dir("GARMIN/sub/New") == "Garmin/sub/New"
    assert twins.made == ["Garmin/sub", "Garmin/sub/New"]
    folders = Listed({"": [Entry("NEWFILES", True), Entry("NewFiles", True)], "NEWFILES": [],
                      "NewFiles": []})
    assert folders.make_dir("NewFiles") == "NEWFILES" and folders.made == []
