# Supported devices

| Device | Connection | Checked by | What works | Report: system, version |
|---|---|---|---|---|
| fēnix 8 (47 mm, Solar) | MTP | the developer, on its Garmin account; [#13](https://github.com/TomaszBojanowski/openExpress/issues/13) | sync, software updates (the system software and components, also in folders the watch hides), maps, Connect IQ apps, music, activities, courses, Device Status and satellite data (experimental): [what was checked](#fēnix-8-47-mm-solar). In #13 it works once gvfs-mtp is installed and Dolphin's MTP service no longer holds the watch (see [System requirements](../README.md#system-requirements)); map downloads and copying work | developer: Fedora 44, GNOME; courses: Fedora 45, GNOME, the version from source after 0.8.7; #13: CachyOS, KDE Plasma, Flatpak 0.6.15 |
| Edge 1050 | MTP | the developer, on the same account | sync, system software updates, the primary training device, Connect IQ apps, courses, Device Status and satellite data (experimental): [what was checked](#edge-1050) | developer: Fedora 44, GNOME; the second update and courses: Fedora 45, GNOME, Flatpak 0.8.7 and the version from source after it |
| Varia RTL515 (part 006-B3592-03) | USB drive | the developer; [#9](https://github.com/TomaszBojanowski/openExpress/issues/9) | detected as a USB drive; sign-in and checking for software updates; shown on the start page as the remembered Varia before its drive is mounted; a component update copied to it and the drive ejected in the window | developer: Fedora 44, GNOME on Wayland; #9: Linux Mint 22.3, Cinnamon on X11, Flatpak 0.6.4 |
| epix (Gen 2; part 006-B3943) | MTP | the developer | read-only commands and backup; it is not registered to the account used for testing, so sync and Connect IQ apps are refused with a message, and the window says why | developer: Fedora 44, GNOME |
| Venu 2 (part 006-B3703) | MTP | the developer | detected automatically; read-only commands; software updates installed (two components, Time Zone Map and Exercise to muscle mapping); added to a Garmin Connect account with **Add to Account** in the window (experimental; software 19.05); it has not been synced, so sync and Connect IQ apps are not tested on it | developer: Fedora 44, GNOME |
| fēnix 8 (47 mm, AMOLED; part 006-B4536-00) | MTP | [#4](https://github.com/TomaszBojanowski/openExpress/issues/4) | detected; sign-in, checking for software updates and Garmin maps (install, update or remove) work; the watch is in Garmin's beta program | Linux Mint 22.3, Cinnamon on X11, Flatpak 0.6.1 |
| fēnix 8 (51 mm, AMOLED; part 006-B4536-00, as the 47 mm one reported) | MTP | [#14](https://github.com/TomaszBojanowski/openExpress/issues/14) | it opens in the window; before its first Connect IQ app it has no app list (`GARMIN/APPS/OUT.BIN`): the Apps page shows no apps, and apps from the account are queued in Garmin Connect | Fedora 44, KDE on Wayland, Flatpak 0.6.19 |
| fēnix 7 (Sapphire Dual Power; part 006-B3909-00) | MTP | [#6](https://github.com/TomaszBojanowski/openExpress/issues/6) | detected; sign-in, checking for software updates, installing a system software update, Garmin maps (install, update or remove) and maps of your own work | Fedora 44, GNOME on Wayland, Flatpak 0.6.2 |
| fēnix 7X (Sapphire Solar) | MTP | a user on Reddit | software and map updates work | Fedora |
| fēnix 7S (Sapphire) | MTP | a user on Reddit | map install works | SteamOS on a Steam Deck |
| fēnix 6 Pro (part 006-B3290-00) | MTP | [#74](https://github.com/TomaszBojanowski/openExpress/issues/74) | detected; sync with Garmin Connect, software updates, maps, Connect IQ apps and music work (software 28.02) | CachyOS, KDE on Wayland, Flatpak 0.12.1 |
| fēnix 5X (part 006-B2604-00) | MTP | [#27](https://github.com/TomaszBojanowski/openExpress/issues/27) | detected; software updates and the downloads of a sync work (software 19.10); uploads, maps and Connect IQ apps not tried yet | Ubuntu 24.04, GNOME on Wayland, Flatpak 0.8.1 |
| Instinct 3 – 45mm Tactical (part 006-B4585-00) | MTP | [#21](https://github.com/TomaszBojanowski/openExpress/issues/21) | detected; sync works, also of an activity with GPS. With Stealth mode on, the watch keeps no GPS track of an activity, so only its totals reach Garmin Connect | Kubuntu 26.04, KDE on Wayland, Flatpak 0.7.0 |
| Edge 1040 (part 006-B3843-00) | MTP | [#8](https://github.com/TomaszBojanowski/openExpress/issues/8), [#10](https://github.com/TomaszBojanowski/openExpress/issues/10) | detected; checking for software updates, installing a Garmin map (Trailforks) and installing and removing Connect IQ apps work; sync not tried. In #10 map updates worked too; the sign-in window works also when the system's language is missing in the Flatpak (see [The Flatpak and the language of your system](troubleshooting.md#the-flatpak-and-the-language-of-your-system)) | #8: Linux Mint 22.3, Cinnamon on X11, Flatpak 0.6.1; #10: Ubuntu 26.04, KDE on X11, Flatpak 0.6.7 |
| Edge 540 (part 006-B4061-00) | MTP | [#18](https://github.com/TomaszBojanowski/openExpress/issues/18), [#28](https://github.com/TomaszBojanowski/openExpress/issues/28) | detected; sync, software updates and maps work. In #28, on Ubuntu 22.04 (flatpak 1.12), a software update and a map install from the Flatpak work (software 30.18) | #18: Fedora 44, KDE on Wayland, Flatpak 0.6.20; #28: Ubuntu 22.04, GNOME on Wayland, Flatpak 0.8.6 with the reporter's fix |
| Drive 53 EX (part 006-B4449-00) | MTP | a user; [#42](https://github.com/TomaszBojanowski/openExpress/issues/42) | detected; its `.System` folder, hidden from the file manager, read over a direct USB connection; the device software 8.50 → 9.00 and the time zone map 43.00 written there from the command line and installed by the device; a reinstall of the time zone map from the window; a map update, City Navigator North America NT 2027.20 (8.3 GB), installed there from the command line with the trial option, and the device then listed 2027.20; not synced with Garmin Connect, as Garmin's server does not take its data | Debian 13, the version from source (test branches; the reinstall in the window and the map update: main) |
| Venu 2S (part 006-B3704-00) | MTP | [#60](https://github.com/TomaszBojanowski/openExpress/issues/60) | detected; read-only commands only, as the watch is on another person's Garmin account: its information, files (each data type found) and activities read, no maps on it, and the check for software updates lists its eight font updates (software 19.05 when reported); nothing installed or synced, and Connect IQ apps not checked | Ubuntu 26.04, openExpress 0.11.1 from source |
| Venu X1 (part 006-B4603-00) | MTP | [#70](https://github.com/TomaszBojanowski/openExpress/issues/70) | detected; sync with Garmin Connect, software updates (the watch software and components, also into folders the watch hides, written over a direct USB connection) and Garmin maps work (software 19.23); Connect IQ apps and music not tried yet | Fedora 45, KDE Plasma on Wayland, Flatpak 0.12.3 |
| Forerunner 970 (part 006-B4565-00) | MTP | [#72](https://github.com/TomaszBojanowski/openExpress/issues/72) | detected; sync with Garmin Connect, software updates, maps, Connect IQ apps and music work (software 19.23) | CachyOS, KDE on Wayland, Flatpak 0.12.1 |
| Forerunner 955 (part 006-B4024-00) | MTP | [#86](https://github.com/TomaszBojanowski/openExpress/issues/86) | detected; sync with Garmin Connect, software updates, maps and Connect IQ apps work (software 29.52); music not tried | Linux Mint 22.3, Cinnamon on X11, Flatpak 0.13.3 |
| vívoactive 5 (part 006-B4426-00) | MTP | [#73](https://github.com/TomaszBojanowski/openExpress/issues/73) | detected; software updates and music work (software 17.05); sync and Connect IQ apps not tried, as the watch is not on the reporter's Garmin account; it lists no maps for exchange with a computer | CachyOS, KDE on Wayland, Flatpak 0.12.1 |
| inReach Mini 2 (part 006-B3968-00) | USB drive | [#11](https://github.com/TomaszBojanowski/openExpress/issues/11) | connects as a USB drive: software updates work (the device installs one once its drive is ejected properly); not synced: Garmin's update server says that Garmin Connect does not take its data ([#12](https://github.com/TomaszBojanowski/openExpress/issues/12)) | Linux Mint 22.3, Cinnamon on X11, Flatpak 0.6.4 |
| Forerunner 55 (part 006-B3869-00) | USB drive | [#19](https://github.com/TomaszBojanowski/openExpress/issues/19) | connects as a USB drive: detected; software updates and sync with Garmin Connect work (software 11.03 when reported) | Fedora 44, KDE on Wayland, Flatpak 0.6.20; sync: version not given |
| Forerunner 920XT (part 006-B1765-00) | USB drive | [#20](https://github.com/TomaszBojanowski/openExpress/issues/20) | connects as a USB drive: software updates work (time-zone map updated), sync not confirmed yet | Fedora 44, KDE on Wayland, Flatpak 0.6.20 |
| Instinct 2S Solar (part 006-B3889-00) | USB drive | [#24](https://github.com/TomaszBojanowski/openExpress/issues/24) | connects as a USB drive; sync works (new activities uploaded, those already in Garmin Connect recognised, no errors); installing the software update of its GPS chip is not confirmed yet | Arch Linux, GNOME on X11, Flatpak 0.7.13 |
| Forerunner 45 (part 006-B3282-00) | USB drive | [#36](https://github.com/TomaszBojanowski/openExpress/issues/36), [#30](https://github.com/TomaszBojanowski/openExpress/issues/30) | connects as a USB drive: detected; sync and software updates work (software 5.40); satellite data (experimental): written by a test version, read back whole and taken by the watch, which already had current data before | CachyOS, KDE on Wayland, Flatpak 0.10.0 |
| Edge 820 (part 006-B2530-00) | USB drive | [#23](https://github.com/TomaszBojanowski/openExpress/issues/23) | connects as a USB drive: detected; reading its files and activities, a backup and a Time Zone Map update (installed) work (software 12.70 when reported); sync checked only as its plan (a dry run); maps are refused, as on every device in this mode | Ubuntu 26.04, openExpress 0.11.0 from source |
| inReach Mini (1st generation; USB 091e:0b89) | HID (a USB drive for its first minute) | [#31](https://github.com/TomaszBojanowski/openExpress/issues/31) | not supported: it connects with an HID interface and, for about a minute, a small USB drive, then with HID only; it offers neither MTP nor a drive that stays. `openexpress info` says at once that openExpress does not support its connection; `openexpress report` works | Arch Linux ARM (Asahi, aarch64), Hyprland; 0.8.8 from source; 0.8.9 (pip) |
| zūmo 595LM | – | [#15](https://github.com/TomaszBojanowski/openExpress/issues/15) | detected; software updates and map updates work; the device itself has no Garmin Connect sync or Connect IQ apps | the command line only, in a container on a home server, also built for ARM on a Raspberry Pi; a version before 0.6.20 |
| Other Garmin watches and bike computers connecting over USB (MTP) | MTP | – | expected to work, untested | – |

Tried openExpress with another device? Please send a
[device report](https://github.com/TomaszBojanowski/openExpress/issues/new?template=device_report.yml) –
also when everything works: [Reporting a problem](troubleshooting.md#reporting-a-problem).

## The developer's fēnix 8 and Edge 1050

### fēnix 8 (47 mm, Solar)

Checked by the developer over MTP:

- Sign-in and sync: uploads, files from Garmin Connect, TrueUp. The dry run of `sync` lists the
  files Garmin Connect has queued for the watch (a workout sent from Garmin Connect, which the
  sync then wrote to the watch).
- Software updates downloaded. A component update (a map support file) installed from the window
  in the Flatpak: **Update ready** until the watch was unplugged, up to date once it was plugged
  in again, and no longer offered. The system software 23.39 → 24.20 installed from the window.
  Voice prompts (154 MB) and a font, which the watch keeps in folders hidden from the file
  manager, installed over a direct USB connection (the voice prompts on the
  command line, the font in the window, and the font installed again in the window over the
  file there): the watch took each once unplugged, and they are no longer offered.
- Garmin maps: install, update and removal, on the command line and in the window. In the
  Flatpak a Garmin Ski Map update, with the new version on the watch after it was unplugged and
  plugged in again.
- Maps of your own: added on the command line and in the window, removed in the window; also in
  the Flatpak.
- Connect IQ apps: install and removal on the command line and in the window, the order restored
  on the command line; in the Flatpak with its USB permission.
- Music and playlists copied.
- The primary training device set; the watch's diagnostic files copied.
- Satellite data (experimental): with **Satellite Data** turned on, a sync wrote `GPE.bin` and
  `GPES.bin` as Garmin's server sends them; the watch unpacked both once unplugged, their
  contents the same as Garmin's files.
- Activities: the **Activities** page and `openexpress activities`; GPX export in the window and
  with `openexpress activities export`; `openexpress fit inspect`.
- The page of an activity:
  - its summary tiles;
  - the map of its track: streets from OpenStreetMap once turned on, the track coloured by
    the chart's series, the tiles cleared with `openexpress cache`;
  - its chart (heart rate, elevation, pace) and its laps (the one chosen shown on the chart and
    the map);
  - its heart rate zones (the time in each), training effect and load, and details, the same as
    in Garmin Connect; the moving time, worked out from the records, within 1%.
  - not checked yet for a ride (speed, cadence in rpm).
- Courses from GPX files (experimental, on the command line): a track copied as `.GPX`, as the
  watch declares, waited in `GARMIN/NewFiles` while the watch was connected and became a course,
  named as in the file, once the watch was unplugged; it was listed as copied by openExpress and
  removed. A course sent from Garmin Connect is listed as such, with the name, distance and
  ascent Garmin Connect shows.
- Device Status in the window: the page with five crashes of the same error and the storage by
  kind of file; Free Up Space removed five older memory dumps (44.9 MB) after copying them, the
  checksums of the copies matching, and the newest dump stayed; the page from the records once
  the watch was unplugged, and the ordinary page again once it was plugged in.
- Not tested on this watch yet: updating a Connect IQ app, **Copy** in the row **Crash Logs** of
  the Device Status page (over a direct USB connection).

### Edge 1050

Checked by the developer over MTP, on the same account:

- Detected automatically; read-only commands and backup.
- Sync in the window: new files uploaded, files from Garmin Connect written to the device.
- Software updates: three system software updates installed in the window (betas 33.12, 33.20
  and 33.21).
- Made the primary training device, and back.
- Connect IQ apps: an app updated; a data field installed and removed on the command line.
- Courses from GPX files (experimental):
  - copied, each as `.GPX` as the Edge declares: a track, a file of two tracks and a file of
    waypoints from the command line, and a route from the window, its course renamed in the
    confirmation dialog;
  - they became four courses and three saved locations only once the Edge was unplugged; the
    count of saved locations is the same as the Edge's;
  - the courses were listed as copied by openExpress and removed, on the command line and in the
    window.
- Satellite data (experimental): the Edge keeps `GPES.bin` where the file manager shows it, and
  no `GPE.bin`. With **Satellite Data** turned on, a sync wrote `GPES.bin` as
  Garmin's server sends it, and the Edge unpacked it by itself about two minutes after it was
  unplugged. Plugged in again within a minute, the file still waited, not unpacked yet, and the
  switch stayed on.
- Device Status in the window: the page without crashes; Free Up Space removed five older memory
  dumps (682.8 MB): two after copying them (75.3 MB, the checksums of the copies matching) and
  three without a copy, as too large to copy (607.5 MB); the newest dump stayed; the page from the
  records once the Edge was unplugged, and the ordinary page again once it was plugged in.

Removing the file of an update a device did not install (**Free Up Space** on the Device Status
page) was checked only on a test USB drive laid out as a Garmin device, not on a real device.

## USB modes: MTP and mass storage

### MTP

Devices that connect over MTP (most watches and bike computers, such as the fēnix 8 and the
Edge 1050) work in both the Flatpak and the version from source.

### Mass storage (USB drive)

Some Garmin devices connect as a USB drive: some watches, such as the Forerunner 55, older
models, the inReach Mini 2 and accessories such as the Varia RTL515. If you are not sure which
mode your device uses, plug it in: if it appears as a USB drive in your file manager rather than
through MTP, it is in mass storage mode.

What openExpress does with such a device, in the window and on the command line, in the Flatpak
and in the version from source:

- Finding it: the window and the command line find it by themselves. The window gives it a tile
  of its own, also when the system has not mounted it yet (as on KDE, until a file manager opens
  it), and mounts it when you choose it. When an MTP device is connected too, the command line opens the MTP device.
  `--path` is only for a folder without a device, such as a copy.
- Its information, software updates and backups, as for any other device.
- Sync, both ways, as over MTP: its new files are uploaded and the files Garmin Connect queues
  for it (courses, workouts, training plans, TrueUp) are written to it, with the **Sync** button
  or `openexpress sync`, never in the background. Connect IQ apps queued for it stay in Garmin
  Connect's queue. After a sync that wrote files to it the drive is ejected (the window asks
  first), so that the device takes them.
- Which drives are synced: one whose `GarminDevice.xml` declares data that goes to Garmin
  Connect, when Garmin's update server answers without saying that Garmin Connect does not take
  its data. A Varia declares none; for an inReach Mini 2 the server says Garmin Connect does not
  take it; without an answer of that server a drive is not synced either. When a drive is not
  synced, the device page and the command say why.
- Apps, maps and music are not supported; the window and the commands say so
  ([Limitations](../README.md#limitations)). A car or motorcycle navigator that connects as a USB
  drive (such as an older nüvi) should therefore get its software updates, but no map updates.
- Once its drive is ejected, a watch in this mode switches to its Garmin USB mode, which
  openExpress does not use: unplug the cable and plug it in again to use it again.

Tested in this mode by the developer on the Varia RTL515 (also copying a component update and
ejecting the drive in the window), and by users on the devices marked *USB drive* in the table.
Reports from other devices in mass storage mode are welcome.

### HID only

A Garmin device that connects with an HID interface and nothing else, or with HID beside a USB
drive (such as the inReach Mini of the first generation, whose drive goes away after about a
minute), offers neither MTP nor a drive that stays: openExpress cannot use it, and the window
and the commands say so at once (*Device Not Supported*). A device report still works.
