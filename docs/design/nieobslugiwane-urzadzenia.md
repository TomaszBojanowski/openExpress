# Design: a Garmin device that openExpress cannot use, and a report without a device

Status: decisions D1–D6 in section 4 are made (each as proposed, D4 with the reason first);
the steps of section 8 are being implemented. Started by #31 (an inReach Mini of the first
generation).

## 1. Scope

In scope:

1. **Recognising a Garmin device that offers neither MTP nor a lasting USB drive** (HID alone,
   or HID beside a drive), and telling the user at once, in the window and on the command line,
   that it is not supported: no 45 s wait, no advice about a PIN or about KDE and Dolphin.
2. **`openexpress report` and `report --log` always work**: when no device can be opened (not
   ready, not supported, none on USB), the report says what can be found without opening one,
   and `--log` gives the log. The window's *Report a Device or Problem…* does the same.

Out of scope: any support for such a device (reading it, updates over HID), and any change to
`_mass_storage_only()`, which decides what counts as a Garmin drive.

## 2. What happens today

- `usb_state()` sorts the Garmin devices on USB (vendor 091e) by reading `/sys` only: a
  device whose interfaces are all mass storage (class 08) is a drive (`MASS_STORAGE`); 091e:0003
  is a device starting (`STARTING`) or, where a drive was, one in Garmin USB mode
  (`DRIVE_OFF`); anything else is a device in its normal mode (`PRESENT`), which gvfs is
  expected to offer over MTP.
- An inReach Mini of the first generation (091e:0b89, "Garmin USB inReach") comes up with two
  interfaces, HID (class 03) and mass storage (class 08, a removable disk of 6 MiB). After about
  70 s it leaves USB on its own and comes back with the HID interface only, and stays so. Its
  real work with the computer seems to go over HID. Either way it is not a drive by
  `_mass_storage_only()` (HID beside it), so it is `PRESENT`.
- `PRESENT` is waited for: the window shows *Device Starting…* for 45 s
  (`WAIT_FOR_DEVICE_S`), then *Device Not Ready* with the advice about a PIN and about KDE's MTP
  service (`held_elsewhere_text()`); the command line waits 45 s and ends with the same words,
  exit code 1.
- `openexpress report` opens the device first (`open_device`), so with no device to open it
  ends with that error; `--log` never gets to the log. The window's dialog says *Connect the
  device first* and shows no report.
- A mounted drive of vendor 091e is opened when it has `GARMIN/GarminDevice.xml`
  (`garmin_ums`, `find_ums_devices`), whatever other interfaces the device has. Whether the
  Mini's 6 MiB drive has that file is not known.

## 3. Recognising it

A Garmin device on USB is **not supported** when the classes of its interfaces can be read,
one of them is HID (03), and every one is HID or mass storage (08). So:

| Interfaces (class) | Today | With this design |
|---|---|---|
| HID + mass storage (03, 08): inReach Mini 1st gen., first minute | `PRESENT`, waited for | not supported |
| HID only (03): inReach Mini 1st gen. afterwards | `PRESENT`, waited for | not supported |
| mass storage only (08): Varia RTL515, inReach Mini 2 (#11), Instinct 2S (#24), Forerunner 55 as a drive | `MASS_STORAGE` | unchanged |
| PTP/MTP (06), or vendor-specific (ff): fēnix 8, epix, Venu 2, Edge 1050 | `PRESENT` | unchanged |
| 091e:0003 (vendor-specific, ff): starting, or Garmin USB mode | `STARTING` / `DRIVE_OFF` | unchanged |
| mass storage + vendor-specific (08, ff) | `PRESENT` | unchanged |
| class not readable, or no interface | `PRESENT` | unchanged |

A device that offers MTP has a PTP/MTP or vendor-specific interface, so no watch, cycling
computer or drive known today can fall into the new state, whatever else it offers beside.
Only a class that can be read counts: a folder without it never makes a device unsupported. If
a device were HID-only for a moment while it starts, the window, which reads `/sys` every few
seconds, shows the new state only until the device comes back in its normal mode; that has not
been seen on any device.

## 4. Decisions

**D1. The rule.** (a) HID present, every interface HID or mass storage (section 3);
(b) a list of product IDs (091e:0b89); (c) HID present and no PTP/MTP or vendor-specific
interface. *Proposed: (a).* It needs no list, holds for the HID-only state as well, and is
narrower than (c): a device with an interface of a class not named here is waited for as
today.

**D2. Next to other Garmin devices.** The new state `UNSUPPORTED` is the state of `usb_state()`
only when the unsupported devices are the only Garmin devices on USB. With a watch in its normal
mode or starting, that one is waited for as today; with a Garmin drive, `MASS_STORAGE` as
today. The log names every device either way. *Proposed.*

**D3. The drive of an unsupported device** (the Mini's first minute). (a) Not opened, also when
the system mounts it and it has `GARMIN/GarminDevice.xml`: one line in the log; (b) opened as a
drive like any other. *Proposed: (a)*, until it is known what is on that drive: it goes away
after about a minute, mid-sync perhaps, and updates do not go through it.

**D4. The words** (chosen): first, in short, why; then the device, named by the product
string it gives USB (`/sys/…/product`, e.g. "Garmin USB inReach") and its USB ID ("Garmin
device" without a product string); then how to report it, the menu item named with the menu's
own words.
- Title (window): **Device Not Supported** / **Nieobsługiwane urządzenie**.
- The start page, under the title, one line: *Garmin USB inReach · Unsupported connection* /
  *Garmin USB inReach · nieobsługiwane połączenie*. The whole text is on the pages of the
  device view.
- The pages of the device view: *openExpress works with devices that connect as a media device
  (MTP) or a USB drive. This one uses a different connection. Connected: {name} (USB {id}). To
  report it, choose “Report a Device or Problem…” in the main menu.* / *openExpress działa
  z urządzeniami, które łączą się jako urządzenie multimedialne (MTP) lub dysk USB. To
  urządzenie używa innego połączenia. Podłączone: {name} (USB {id}). Aby to zgłosić, wybierz
  „Zgłoś urządzenie lub problem…” w menu głównym.*
- Command line: the same, ending with *To report it, run openexpress report.* / *Aby to
  zgłosić, uruchom openexpress report.* A separate msgid from the window's (the rule for texts
  shared with the command line).
- The icon of the notice, on the start page and on the pages: the warning
  (`dialog-warning-symbolic`), not the program's icon, whose arrows would say that something
  is on its way.

**D5. The command line.** At once, without the wait: a notice, not an error (no "Error:", as
`MassStorageNotice`), exit code 1 ("an error or a refusal: the device…", `docs/cli.md`); no new
exit code. *Proposed.* (b) A new exit code for "not supported": nothing would read it today.

**D6. The report without a device.** When no device can be opened, `report` prints a report
with what can be found without opening one (section 6) and exits with 0; `--log` prints the log
as always; the window's dialog shows the same report, with the device section saying why none
is open. *Proposed.* (b) Only the command line: the window would still say *Connect the device
first*.

## 5. What the window and the command line do

**Window.** While no device is open, the notice on top of the start page shows *Device Not
Supported* with its one line, and the placeholder of the pages of the device view
(`_usb_notice`) the whole words of D4, both at once, with the warning icon and without a
spinner; nothing is waited for and the KDE advice is never added. When the Mini leaves USB after its first minute and comes
back with HID only, the placeholder shows *Connect Your Device* for that moment and then the
same notice again. The menu's *Report a Device or Problem…* gives the report of section 6.
No page of a device opens, as nothing is opened.

**Command line.** Every command that opens a device (`info`, `status`, `sync`, `updates`…)
ends at once with the notice of D4 and exit code 1 (D5). `report` and `report --log` give
the report without a device (D6). `devices` lists the remembered devices only: unchanged.

**Log.** The line of the USB state names the new state like the others ("device: not
available after 0.0 s, USB state: unsupported (091e:0b89), waiting: …"), and once for each such
device that comes: "USB: a Garmin device 091e:0b89 with interfaces 03, 08: HID without MTP, not
supported". No product string, no serial number, no place on USB in the log.

## 6. The report without a device

The same report as with a device, with a device section that says what is known without
opening one:

```
### Device
- Not opened: not supported (HID without MTP)
- Garmin devices on USB: 091e:0b89 "Garmin USB inReach", interfaces: HID (03), mass storage (08)
- MTP support in gvfs: yes

### System
- openExpress: … (source)
- OS: …
- Desktop: … (wayland)
- Python: …
- Logged in to Garmin Connect: no
```

- *Not opened*: why, from the state that stopped the opening: not supported; not ready (on USB,
  not offered over MTP); starting; a drive not mounted; several devices; none on USB.
- *Garmin devices on USB*: for each, its USB ID, its product string and the classes of its
  interfaces, named (HID, mass storage, PTP/MTP, vendor-specific, other). Only `idVendor`,
  `idProduct`, `product` and `bInterfaceClass` are read: never `serial`, never the place on USB.
- The features section of a device report (its data types) is left out: there is no
  `GarminDevice.xml` to declare them.
- The address that fills in the form is made as today; its title uses the product string.
- `--log`: the end of the log, filtered as always (`applog.redact`); nothing else changes.

## 7. Tests (made-up devices in a made-up `/sys`)

- The table of section 3, each row: the state of `usb_state()`; a fēnix (06), a device in
  vendor mode (ff), 091e:0003, a drive (08), mass storage with vendor-specific (08, ff), an
  unreadable class: never the new state.
- Next to a watch and next to a drive (D2).
- The command line: at once (`clock.slept == 0`), the words of D4, no "PIN", no "kiod6", exit
  code 1, no "Error:".
- The window's placeholder: title and words, not busy, the same from the first poll on; also
  after the device leaves USB and comes back with HID only.
- A mounted drive of an unsupported device with `GARMIN/GarminDevice.xml` is not opened (D3).
- The report without a device: its device section for each reason of section 6; a made-up
  `serial` file beside the others never appears in it; `report --log` prints the log; exit
  code 0; the window's dialog shows it.
- Texts: the window at 360 px; the Polish ones with hard spaces.

## 8. Steps

1. Recognising it (sections 3 and 5, D1–D5): the state, the words, the window's placeholder,
   the command line, the drive of D3, the log; tests first. Previews of the placeholder, in
   English and Polish, at 900 and 360 px.
2. The report without a device (section 6, D6): the command line and the window's dialog;
   tests first. A preview of the dialog.
3. Documents: `docs/troubleshooting.md` (a device that is not supported; the report without a
   device), `docs/cli.md` (`report` without a device), `docs/devices.md` (the inReach Mini of
   the first generation as not supported).

## 9. Open questions

- What the 6 MiB drive holds in its first minute, and whether `GARMIN/GarminDevice.xml` is on
  it: asked of the reporter. If it is there, a report could name the model and its software
  (D3 then to be looked at again).
- The full USB descriptors (`lsusb -v -d 091e:0b89`): whether the HID interface is all there
  is to the device afterwards; asked of the reporter.
