# Design: an update the device did not take in

Status: implemented, with the heading of the state "Update Not Installed by the Device" (PL
„Urządzenie nie zainstalowało aktualizacji”) instead of the one proposed; the row "Device Software"
as proposed.

## 1. The case

An Instinct 2S Solar connected as a USB drive (#24). Garmin's update server offers it the software
of its GPS chip, 006-B3799-11 "Instinct 2/2S GPS Software" 6.70, for `GARMIN/REMOTESW/GUPDATE.GCD`;
its `GarminDevice.xml` lists that part at 6.10, and the watch shows GPS 6.1.

- openExpress copied the file. After the watch was unplugged, the file was changed in place (the same
  size, other bytes: "MD5 differs"), and the GPS version stayed 6.10.
- At a later connection the file on the watch matched the offered one again. The window said
  "Update ready: eject the device to install it"; the drive was ejected and the watch unplugged
  without an error; the watch did nothing. The phone's Garmin Connect says no update is available,
  which proves nothing: the app does not always show the updates there are.
- After an earlier eject, with the cable still in, the window said that the watch had switched to
  its Garmin USB mode: "Unplug the cable and plug it in again."

Before this change:

- An update whose file is on the device as offered (`updates.presence`: MATCHES) and which
  openExpress copied (`copied_at`) or which the device installs once disconnected is "ready"
  (`updates.staged`): the top of the Updates page and the row "Device Software" say "Update ready:
  eject the device to install it" (over MTP: "unplug"), its tile says "Eject to install", the page
  has Eject, and `openexpress updates` says "To install it, eject the drive in the file manager,
  then unplug the device." Every connection says this again, however many times the device was
  unplugged since: it asks for one more eject that does not help.
- A copied update whose file changed in place shows "The device changed or refused the file copied
  {when}" with Reinstall; a copied update whose file is gone shows "Copied {when}, check on the
  device that it is installed".
- After an eject: "Done" – "The device is ejected. You can unplug the cable." If the cable stays in
  and the watch comes back on USB as 091e:0003 at the place of its drive, the start page says
  "After it was ejected, the watch switched to its Garmin USB mode, which openExpress does not use.
  Unplug the cable and plug it in again." – also when an update waits, which the watch installs only
  while it is unplugged.

## 2. The request for updates

`POST api/device-software/v1/updates` with `garminDeviceXml` (the device's `GarminDevice.xml`, read
from the drive and sent unchanged), `isUserInteractive: true` and `clientInfo: {}`; on the Updates
page with `?filter=all`, so that reinstalls come too. Garmin Express sends the same three fields
(`isUserInteractive` false in its own background checks) and asks with `filter=all` for the list of
reinstalls (Windows; not on macOS). openExpress describes no component itself: the server reads the
parts and their versions from the `UpdateFile` entries of the XML; openExpress reads them only to
show "6.10 → 6.70". The answer is kept by the XML's content and forgotten after a copy.

What this says of the hypotheses of #24 (PLAN.md):

- The server offers 6.70 because the XML lists 6.10: the same XML would give Garmin Express the same
  offer. A difference can only come from `isUserInteractive` (always true here) or `filter=all`
  (always here); what the server does with them is not known. The GPS item comes as an update, not as
  a reinstall (the log counts it among the offered ones).
- On a drive the XML is the file the watch wrote; if the watch rewrites it only when it starts, an
  installed part would still be listed at its old version until then. Here the watch itself shows
  GPS 6.1, so 6.70 is not installed.

## 3. When the device has had its chance

The device has had its chance to take an update in once it was unplugged after openExpress copied
it. What openExpress can see of that, from /sys only:

- **The USB address alone is no signal.** The bus and device number change at every
  re-enumeration: at a plug-in, but also when a watch ejected as a drive switches to its Garmin USB
  mode (091e:0003) on the cable, and when it comes back. The place on USB (the port, the name of its
  folder in /sys) stays the same then; the program already told the Garmin USB mode by the place,
  not by the number. Before this change the log line "the drive ejected by openExpress was
  unplugged" (taken from the same bus and device number) was written also when the watch only
  switched to its Garmin USB mode, the cable still in; it follows the rule below now.
- **Signal 1 – an unplugging seen (the one relied on).** A running openExpress (the window or the
  background sync) sees the place of a drive it ejected left empty for at least 5 s: the cable
  went out. Kept with the copy in `known_devices.copied_updates` (the time it was
  seen), so that a later connection, also in another run, knows it. A switch to the Garmin USB mode
  and back keeps the place busy, so it is not taken for an unplugging; a cable out and in again
  within 5 s is not seen by this signal (signals 2 and 3).
- **Signal 2 – another address without the Garmin USB mode.** A new connection of the device at the
  same place with another device number counts only when openExpress watched that place without a
  break since the copy and saw no 091e:0003 there meanwhile (the cable out and in again within
  5 s, or the device restarted). Not across two runs of the program: what happened between
  them is not known.
- **Signal 3 – the drive at another port.** While the place of a drive it ejected is empty, the
  same device (vendor, product and USB serial, or no serial for a device that gives none) is on USB
  at another place: the cable went out and into another port. Counted at once, without waiting the
  5 s, so that the window opens it as any device plugged in and says nothing of an ejected drive. Another device of the same model (another serial) is not taken for it.
- **No signal.** openExpress did not run while the cable went out, the place is not known, or the
  device connects over MTP (where 091e:0003 is also its usual starting mode): nothing is claimed,
  and the update stays "ready" as before this change.

Only drives ejected by openExpress get signal 1; a device over MTP keeps the state before this
change until a signal of its own is designed.

## 4. The state "copied, not installed"

An update is "copied, not installed" when all of these hold: the server still offers it (not as a
reinstall), openExpress copied it, the device has had its chance (section 3), and its file is on the
device as copied (MATCHES) or changed in place (MD5 differs). It is no longer "ready".

| Where | EN | PL |
|---|---|---|
| Top of the Updates page, title | Update Not Installed by the Device | Urządzenie nie zainstalowało aktualizacji |
| Under it: name and versions | {name} {old} → {new} | {name} {old} → {new} |
| Line, the file as copied | Copied {when}. The device was unplugged since and still reports version {old}; the file is still on it, unchanged. | Skopiowano {when}. Urządzenie było od tego czasu odłączone i nadal podaje wersję {old}; plik wciąż na nim jest, bez zmian. |
| Line, the file changed | Copied {when}. The device was unplugged since, changed or refused the file and still reports version {old}. | Skopiowano {when}. Urządzenie było od tego czasu odłączone, zmieniło albo odrzuciło plik i nadal podaje wersję {old}. |
| Advice, once | Check the version on the device (Settings → System → About). Ejecting it again does not help; a device report helps find out why. | Sprawdź wersję na urządzeniu (Ustawienia → System → Informacje). Ponowne wysunięcie nie pomoże; raport o urządzeniu pomoże ustalić dlaczego. |
| Row "Device Software" (device page, remembered device) | Update copied, not installed by the device | Aktualizacja skopiowana, urządzenie jej nie zainstalowało |
| Row of such a component in the list | the two lines above as its subtitle | – |
| `openexpress updates`, under the item | Copied {when}; the device was unplugged since and has not installed it (the file is still on it). / … and changed or refused the file. | Skopiowano {when}; urządzenie było od tego czasu odłączone i jej nie zainstalowało (plik wciąż na nim jest). / … i zmieniło albo odrzuciło plik. |

- Buttons: no Eject and no Install. The file as copied: none (copying it again changes nothing);
  the file changed: Reinstall, as before. Optional: **Report a Device or Problem…** on the page.
- Tile: no chip for it (neither "Eject to install" nor "Update available").
- `openexpress updates`: no "To install it, eject…" line for it; `--install all` leaves it out when
  its file is there as copied. Exit codes unchanged.
- Log: "software update: {part} {version} copied {date}, on the device after an unplug ({matches |
  other bytes}): not installed by the device".
- Several updates: one "copied, not installed" beside others to install – the top says what is to
  install, and that one is a row of the list with its lines.

## 5. After an eject with an update waiting

When the eject follows a copied update that waits for the device ("ready"):

| Where | EN | PL |
|---|---|---|
| "Done" after the eject (window), and the command line | The device is ejected. Unplug the cable and leave the device unplugged so it can install the update. | Urządzenie zostało wysunięte. Odłącz kabel i zostaw urządzenie odłączone, aby mogło zainstalować aktualizację. |
| Start page and command line, the watch in its Garmin USB mode (091e:0003) | After it was ejected, the watch switched to its Garmin USB mode. Unplug the cable and leave the device unplugged so it can install the update. | Po wysunięciu zegarek przełączył się w swój tryb Garmin USB. Odłącz kabel i zostaw urządzenie odłączone, aby mogło zainstalować aktualizację. |

Without a waiting update (the files of a sync, an eject from the Updates page with nothing
copied), both texts stay as they are. Which applies is kept with the ejected drive (a flag "an
update waits"), as its USB place is.

## 6. Tests

- `staged` leaves out a copied update whose file matches or changed once an unplugging was seen
  (signal 1) or the address changed without the Garmin USB mode (signal 2); it keeps it without a
  signal, and after a switch to the Garmin USB mode and back on the cable.
- The place of an ejected drive empty for 5 s is an unplugging; 091e:0003 at that place, and back,
  is none (also for the log line of an ejected drive unplugged).
- The cable out and in again within 5 s, at the same port or at another: the drive opens at
  once and the window does not say "Device Ejected"; another device of its model at another port
  is not taken for it; the drive named for testing (`OPENEXPRESS_TEST_DRIVE_USB`) is seen back as
  a watch is.
- The Updates page of such an update: the title and lines of section 4, no Eject, no Install
  (matches) or Reinstall (changed); the row "Device Software"; no chip on its tile.
- `openexpress updates`: the line of section 4, no "To install it, eject…"; `--install all` skips it.
- The "Done" text and the Garmin USB mode text with and without a waiting update (section 5).
- The unplugging kept with a copy (known_devices), and a copy from before this change read as before.

## 7. Open

- Whether the Instinct 2S takes a part's update (`REMOTESW`) only when it starts (hypothesis 4):
  then the advice could say to restart the watch without the cable.
- A signal for devices over MTP (their system updates install once unplugged): to be designed; until
  then they keep the state before this change.
