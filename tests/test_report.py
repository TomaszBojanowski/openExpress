from openexpress import report
from openexpress.device.storage import LocalStorage
from openexpress.device.xmldevice import parse


class FakeMtpStorage(LocalStorage):
    label = "mtp://091e_51b4_0000abcdef12/Internal%20Storage"


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


def test_usb_product_id():
    assert report.usb_product_id("mtp://091E_4F42_ABC/x") == "091e:4f42"
    assert report.usb_product_id("/tmp/dir") == "unknown"
