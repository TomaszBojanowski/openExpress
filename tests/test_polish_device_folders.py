"""In Polish a folder on the device is a "folder", as the rest of the program calls it, never a
"katalog"; folders on this computer keep their own words."""

from fakes import in_language

from openexpress.i18n import _

# The texts that name a folder on the device (its own words, never translated here).
DEVICE_FOLDERS = [
    'the app list folder was not found',
    'the GARMIN folder was not found on the device',
    'the app folder was not found on the device',
    ('{name}: the app folder {path} is hidden from the file manager. openExpress will write '
     'the app over a direct USB connection; the device disappears from the file manager for '
     'a moment.'),
    ('{name}: the app folder {path} does not exist on the device. openExpress will try to '
     'create it; if the device refuses, the app is only queued in Garmin Connect and '
     'installed by the phone or Garmin Express.'),
    'The device has no folder {folder}.',
    'Cannot create the folder {path}',
    'Refusing to write into the top folder of the device.',
    'Cannot write {path}: it is a directory',
    'Refusing to delete the root of the device storage',
    'Not a directory: {path}',
    'Map Folder',
    'The map folder could not be read',
    'This device has no folder for maps.',
]


def test_a_folder_on_the_device_is_a_folder_in_polish():
    with in_language("pl"):
        polish = [_(text) for text in DEVICE_FOLDERS]
    assert all(p != text for p, text in zip(polish, DEVICE_FOLDERS, strict=True))  # translated
    assert [p for p in polish if "katalog" in p.casefold()] == []
    assert all("folder" in p.casefold() for p in polish)
