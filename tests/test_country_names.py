"""The name of a country for a map Garmin does not offer in it: from
the ISO 3166 list of the system's iso-codes (also in the Flatpak's runtime), in the program's
language; the code itself when the list or the name is not there. The list here is made up."""

import json

import pytest

from openexpress import i18n


@pytest.fixture
def countries(tmp_path, monkeypatch):
    path = tmp_path / "iso_3166-1.json"
    path.write_text(json.dumps({"3166-1": [
        {"alpha_2": "KR", "name": "Korea, Republic of", "common_name": "South Korea"},
        {"alpha_2": "PL", "name": "Poland"},
        {"alpha_3": "XYZ"},  # another shape: skipped
    ]}))
    monkeypatch.setattr(i18n, "ISO_3166_1", path)
    i18n._countries.cache_clear()
    polish = {"South Korea": "Korea Południowa", "Poland": "Polska"}

    class Translation:
        def __init__(self, words):
            self.words = words

        def gettext(self, text):
            return self.words.get(text, text)

    monkeypatch.setattr(i18n, "_country_translation",
                        lambda language: Translation(polish) if language == "pl" else None)
    yield
    i18n._countries.cache_clear()


def test_a_country_is_named_in_the_language_of_the_program(countries):
    assert i18n.country_name("KR") == "South Korea"  # the common name before the official one
    assert i18n.country_name("pl") == "Poland"
    i18n.setup("pl")
    try:
        assert i18n.country_name("KR") == "Korea Południowa"
        assert i18n.country_name("PL") == "Polska"
    finally:
        i18n.setup("en")


def test_without_a_name_the_code_is_shown(countries, tmp_path, monkeypatch):
    assert i18n.country_name("ZZ") == "ZZ"
    monkeypatch.setattr(i18n, "ISO_3166_1", tmp_path / "missing.json")
    i18n._countries.cache_clear()
    assert i18n.country_name("KR") == "KR"


def test_the_list_of_this_system_if_it_has_one():
    if not i18n.ISO_3166_1.exists():
        pytest.skip("no iso-codes on this system")
    i18n._countries.cache_clear()
    assert i18n.country_name("PL") == "Poland"
