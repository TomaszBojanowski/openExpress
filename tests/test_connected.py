"""The store of the devices known to be plugged in (openexpress/gui/connected.py): each
method alone, then the two ways the window goes through it. No display, no GTK; the entries
stand in for the detection's (only their address is read). All values are made up."""

import types

from openexpress.gui.connected import Connected, uris

WATCH = "mtp://091e_51b4_0000abcdef12/"
EDGE = "mtp://Garmin_Edge_1050_0000abcdef34/"
VARIA = "file:///run/media/made-up-user/GARMIN/"


def entry(uri, name="", ok=True):
    return types.SimpleNamespace(found=types.SimpleNamespace(uri=uri), description=name, ok=ok)


def two():
    return [entry(WATCH, "Test watch"), entry(EDGE, "Edge 1050")]


def opens(uri):
    """is_open of the window: the entry of this address is the device open."""
    return lambda c: c.found.uri.rstrip("/") == uri.rstrip("/")


def test_nothing_is_known_at_first():
    c = Connected()
    assert (c.devices, c.choosing, c.usb_when_listed, c.drawn) == ([], False, None, frozenset())


def test_the_addresses_lose_their_trailing_slash():
    assert uris([entry(WATCH), entry(EDGE.rstrip("/"))]) == {WATCH.rstrip("/"),
                                                             EDGE.rstrip("/")}


def test_listing_makes_the_list_to_choose_from():
    c = Connected()
    devices = two()
    c.listing(devices, ["51b4", "5158"])
    assert c.devices == devices and c.devices is not devices  # a list of its own
    assert c.choosing and c.usb_when_listed == ["51b4", "5158"]


def test_stop_choosing_keeps_the_devices():
    c = Connected()
    c.listing(two(), ["51b4", "5158"])
    c.stop_choosing()
    assert not c.choosing and len(c.devices) == 2 and c.usb_when_listed == ["51b4", "5158"]


def test_clear_forgets_the_devices_but_not_the_usb_state_of_the_list():
    c = Connected()
    c.listing(two(), ["51b4", "5158"])
    c.clear()
    assert c.devices == [] and not c.choosing
    assert c.usb_when_listed == ["51b4", "5158"]  # never cleared, as before


def test_keep_if_open_keeps_the_devices_with_the_open_one_among_them():
    c = Connected()
    c.listing(two(), None)
    c.stop_choosing()
    c.keep_if_open(opens(EDGE))
    assert [d.description for d in c.devices] == ["Test watch", "Edge 1050"]


def test_keep_if_open_forgets_the_devices_without_the_open_one():
    c = Connected()
    c.reread(two())
    c.keep_if_open(opens(VARIA))
    assert c.devices == []


def test_keep_if_open_leaves_choosing_as_it_is():
    c = Connected()
    c.listing(two(), None)
    c.keep_if_open(opens(VARIA))
    assert c.choosing  # stop_choosing says when the list is gone


def test_a_device_found_alone_leaves_no_other_known():
    """One of two on the list unplugged, the one left found alone and opened: neither the
    list nor the device gone stays; the open one's tile comes from the session."""
    c = Connected()
    c.listing(two(), ["51b4", "5158"])
    c.found_alone()
    c.keep_if_open(opens(WATCH))
    assert c.devices == [] and not c.choosing
    shown, _notice = c.for_start(entry(WATCH, "Test watch (open)"), opens(WATCH))
    assert [d.description for d in shown] == ["Test watch (open)"]


def test_reread_replaces_the_devices():
    c = Connected()
    c.reread(two())
    c.reread([entry(WATCH, "Test watch")])
    assert [d.description for d in c.devices] == ["Test watch"] and not c.choosing


def test_listed_compares_addresses_with_or_without_the_slash():
    c = Connected()
    c.reread(two())
    assert c.listed(EDGE) and c.listed(EDGE.rstrip("/")) and not c.listed(VARIA)


def test_unread_gives_the_devices_listed_without_their_details():
    c = Connected()
    assert c.unread() == frozenset()
    c.reread([entry(WATCH, "Test watch"), entry(EDGE, ok=False)])
    assert c.unread() == {EDGE.rstrip("/")}
    assert c.listed(EDGE)  # still on the list


def test_is_choice_only_while_there_is_a_list():
    c = Connected()
    c.reread(two())
    assert not c.is_choice(EDGE)
    c.listing(two(), None)
    assert c.is_choice(EDGE) and not c.is_choice(VARIA)
    c.stop_choosing()
    assert not c.is_choice(EDGE)


def test_usb_changed_tells_a_change_once():
    c = Connected()
    c.listing(two(), ["51b4", "5158"])
    assert not c.usb_changed(["51b4", "5158"])
    assert c.usb_changed(["51b4"]) and c.usb_when_listed == ["51b4"]
    assert not c.usb_changed(["51b4"])


def test_for_start_without_a_device_open():
    c = Connected()
    assert c.for_start() == ([], True)  # nothing: the notice of the USB state
    c.listing(two(), None)
    shown, notice = c.for_start()
    assert [d.description for d in shown] == ["Test watch", "Edge 1050"] and notice is False
    c.stop_choosing()
    assert c.for_start() == ([], True)  # no list any more, though the devices are known
    c.listing([], None)
    assert c.for_start() == ([], True)


def test_for_start_with_a_device_open():
    c = Connected()
    c.reread(two())
    shown, notice = c.for_start(entry(EDGE, "Edge 1050 (open)"), opens(EDGE))
    assert [d.description for d in shown] == ["Test watch", "Edge 1050"] and notice is False
    varia = entry(VARIA, "Varia RTL515")
    shown, notice = c.for_start(varia, opens(VARIA))
    assert shown[0] is varia and len(shown) == 3 and notice is False
    c.clear()
    assert c.for_start(varia, opens(VARIA)) == ([varia], False)  # the one device (D7)


def test_drew_and_changed_since_drawn():
    c = Connected()
    assert not c.changed_since_drawn([])
    c.drew(two())
    assert c.drawn == {WATCH.rstrip("/"), EDGE.rstrip("/")}
    assert not c.changed_since_drawn([entry(EDGE), entry(WATCH)])  # the order aside
    assert c.changed_since_drawn([entry(WATCH)])


def test_drawn_stays_older_than_the_devices_read_again():
    c = Connected()
    c.reread(two())
    c.drew(c.devices)
    c.reread([entry(WATCH, "Test watch")])  # read again while another page is shown
    assert c.changed_since_drawn(c.devices) and len(c.drawn) == 2


def test_a_list_a_choice_and_the_device_opened():
    """Two devices listed, the Edge chosen and opened: no list any more, both still known
    (their mounts bring nothing new, the start page shows both), the Edge among them."""
    c = Connected()
    c.listing(two(), ["51b4", "5158"])
    c.drew(c.for_start()[0])
    c.stop_choosing()
    c.keep_if_open(opens(EDGE))
    assert not c.choosing and len(c.devices) == 2
    assert c.listed(WATCH) and c.listed(EDGE) and not c.is_choice(WATCH)
    shown, notice = c.for_start(entry(EDGE, "Edge 1050 (open)"), opens(EDGE))
    assert [d.description for d in shown] == ["Test watch", "Edge 1050"] and notice is False
    assert not c.changed_since_drawn(shown)


def test_the_moment_after_a_failed_choice():
    """Two devices listed, the Edge chosen, its opening failed; the detection after it is
    running. No list to choose from: a mount of the other one removed is not one of the
    choices; the devices are still known; with none open the start page has only the
    notice, as the window gives it today."""
    c = Connected()
    c.listing(two(), ["51b4", "5158"])
    c.stop_choosing()  # open_choice failed
    assert not c.is_choice(WATCH) and c.listed(WATCH)
    assert len(c.devices) == 2 and c.usb_when_listed == ["51b4", "5158"]
    assert c.for_start() == ([], True)
    c.clear()  # the detection found nothing
    assert c.devices == [] and not c.listed(WATCH)
