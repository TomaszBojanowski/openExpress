from openexpress import report
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse


class FakeMtpStorage(LocalStorage):
    label = "mtp://091e_51b4_0000abcdef12/Internal%20Storage"
    usb_id = "091e:51b4"


def test_report_has_no_identifiers(device_root):
    st = FakeMtpStorage(device_root)
    st.label = FakeMtpStorage.label
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    text = report.build(dev, st, logged_in=True)
    assert "Test watch" in text and "006-B0000-00" in text and "23.39" in text
    assert "USB ID: 091e:51b4" in text
    assert str(dev.unit_id) not in text           # unit ID
    assert "0000abcdef12" not in text             # serial number from the MTP address
    assert "mtp://" not in text
    assert "- [x] Activities" in text and "- [ ] Music" in text


def test_usb_id_comes_from_the_storage_not_from_the_address(device_root):
    st = LocalStorage(device_root)
    dev = parse(st.read("GARMIN/GarminDevice.xml"))
    assert "- USB ID: unknown" in report.build(dev, st)  # e.g. --path

    # An address made of the USB strings carries no IDs, only the serial number.
    st.label = "mtp://Garmin_Edge_1050_0000abcdef12/Internal%20Storage"
    assert "- USB ID: unknown" in report.build(dev, st)
    st.usb_id = "091e:5158"
    text = report.build(dev, st)
    assert "- USB ID: 091e:5158" in text
    assert "0000abcdef12" not in text and "mtp://" not in text and "Garmin_Edge" not in text
    st.usb_id = "091e:unknown"
    assert "- USB ID: 091e:unknown" in report.build(dev, st)
