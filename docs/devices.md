# Supported devices

| Device | Connection | Checked by | What works | Report: system, version |
|---|---|---|---|---|
| fēnix 8 (47 mm, Solar) | MTP | the developer, on its Garmin account; [#13](https://github.com/TomaszBojanowski/openExpress/issues/13) | sign-in, sync (upload, files from Garmin Connect, TrueUp), software update download, map install and removal (also in the window), setting the primary training device, copying its diagnostic files, maps of your own (added from the command line and the window, removed in the window; also in the Flatpak), copying music and playlists, Connect IQ apps (install and remove on the command line and in the window, restoring the order on the command line; in the Flatpak with its USB permission). **Not yet tested on the watch:** updating a Connect IQ app, installing a software update. In #13 it works once gvfs-mtp is installed and Dolphin's MTP service no longer holds the watch (see [System requirements](../README.md#system-requirements)); map downloads and copying work | developer: Fedora 44, GNOME; #13: CachyOS, KDE Plasma, Flatpak 0.6.15 |
| Edge 1050 | MTP | the developer, on the same account | detected automatically; read-only commands and backup; a sync in the window uploaded new files and wrote files from Garmin Connect to the device; one system software update installed in the window (beta 33.12); made the primary training device and back; a Connect IQ app updated | developer: Fedora 44, GNOME |
| Varia RTL515 (part 006-B3592-03) | USB drive | the developer; [#9](https://github.com/TomaszBojanowski/openExpress/issues/9) | detected as a USB drive; sign-in and checking for software updates; shown on the start page as the remembered Varia before its drive is mounted; a component update copied to it and the drive ejected in the window | developer: Fedora 44, GNOME on Wayland; #9: Linux Mint 22.3, Cinnamon on X11, Flatpak 0.6.4 |
| epix (Gen 2; part 006-B3943) | MTP | the developer | read-only commands and backup; it is not registered to the account used for testing, so sync and Connect IQ apps are refused with a message, and the window says why | developer: Fedora 44, GNOME |
| Venu 2 (part 006-B3703) | MTP | the developer | read-only commands; it was the first device to get software updates installed with openExpress (two components, Time Zone Map and Exercise to muscle mapping). It was not registered to the Garmin Connect account used for testing, so nothing that needs the account (sync, Connect IQ apps) was tested on it | developer: Fedora 44, GNOME |
| fēnix 8 (47 mm, AMOLED; part 006-B4536-00) | MTP | [#4](https://github.com/TomaszBojanowski/openExpress/issues/4) | detected; sign-in, checking for software updates and Garmin maps (install, update or remove) work; the watch is in Garmin's beta program | Linux Mint 22.3, Cinnamon on X11, Flatpak 0.6.1 |
| fēnix 8 (51 mm, AMOLED; part 006-B4536-00) | MTP | [#14](https://github.com/TomaszBojanowski/openExpress/issues/14) | it opens in the window; before its first Connect IQ app it has no app list (`GARMIN/APPS/OUT.BIN`): the Apps page shows no apps, and apps from the account are queued in Garmin Connect | Fedora 44, KDE on Wayland, Flatpak 0.6.19 |
| fēnix 7 (Sapphire Dual Power; part 006-B3909-00) | MTP | [#6](https://github.com/TomaszBojanowski/openExpress/issues/6) | detected; sign-in, checking for software updates, installing a system software update, Garmin maps (install, update or remove) and maps of your own work | Fedora 44, GNOME on Wayland, Flatpak 0.6.2 |
| fēnix 7X (Sapphire Solar) | MTP | a user on Reddit | software and map updates work | Fedora |
| fēnix 7S (Sapphire) | MTP | a user on Reddit | map install works | SteamOS on a Steam Deck |
| Instinct 3 – 45mm Tactical (part 006-B4585-00) | MTP | [#21](https://github.com/TomaszBojanowski/openExpress/issues/21) | detected; sync works, also of an activity with GPS. With Stealth mode on, the watch keeps no GPS track of an activity, so only its totals reach Garmin Connect | Kubuntu 26.04, KDE on Wayland, Flatpak 0.7.0 |
| Edge 1040 (part 006-B3843-00) | MTP | [#8](https://github.com/TomaszBojanowski/openExpress/issues/8), [#10](https://github.com/TomaszBojanowski/openExpress/issues/10) | detected; checking for software updates, installing a Garmin map (Trailforks) and installing and removing Connect IQ apps work; sync not tried. In #10 map updates worked too; the sign-in window works also when the system's language is missing in the Flatpak (see [The Flatpak and the language of your system](troubleshooting.md#the-flatpak-and-the-language-of-your-system)) | #8: Linux Mint 22.3, Cinnamon on X11, Flatpak 0.6.1; #10: Ubuntu 26.04, KDE on X11, Flatpak 0.6.7 |
| Edge 540 (part 006-B4061-00) | MTP | [#18](https://github.com/TomaszBojanowski/openExpress/issues/18) | detected; sync, software updates and maps work | Fedora 44, KDE on Wayland, Flatpak 0.6.20 |
| inReach Mini 2 (part 006-B3968-00) | USB drive | [#11](https://github.com/TomaszBojanowski/openExpress/issues/11) | connects as a USB drive: software updates work (the device installs one once its drive is ejected properly), sync not yet ([#12](https://github.com/TomaszBojanowski/openExpress/issues/12)) | Linux Mint 22.3, Cinnamon on X11, Flatpak 0.6.4 |
| Forerunner 55 (part 006-B3869-00) | USB drive | [#19](https://github.com/TomaszBojanowski/openExpress/issues/19) | connects as a USB drive: software updates work, sync not yet | Fedora 44, KDE on Wayland, Flatpak 0.6.20 |
| Forerunner 920XT (part 006-B1765-00) | USB drive | [#20](https://github.com/TomaszBojanowski/openExpress/issues/20) | connects as a USB drive: software updates work (time-zone map updated), sync not yet | Fedora 44, KDE on Wayland, Flatpak 0.6.20 |
| zūmo 595LM | – | [#15](https://github.com/TomaszBojanowski/openExpress/issues/15) | detected; software updates and map updates worked; the device itself has no Garmin Connect sync or Connect IQ apps | the command line only, in a container on a home server, also built for ARM on a Raspberry Pi; a version before 0.6.20 |
| Other Garmin watches and bike computers connecting over USB (MTP) | MTP | – | expected to work, **untested** | – |

Tried openExpress with another device? Please send a
[device report](https://github.com/TomaszBojanowski/openExpress/issues/new?template=device_report.yml) –
also when everything works. In the window: main menu (☰) → **Report a Device or Problem…**
→ **Open on GitHub** opens the form with the report and a title filled in; or run
`openexpress report`, which prints the report and an address that does the same. The report
contains no serial number, unit ID or e-mail. Nothing is published until you submit the form.

## USB modes: MTP and mass storage

Devices that connect over MTP (most watches and bike computers, such as the fēnix 8 and the
Edge 1050) work in both the Flatpak and the version from source. openExpress also supports
Garmin devices that connect as a USB drive (mass storage mode: some watches, such as the
Forerunner 55, older models, the inReach Mini 2 and accessories such as the Varia RTL515): their information and software updates, in
the window and on the command line, in the Flatpak and in the version from source. Both find
such a device by themselves. The window gives it a tile of its own, also when the system has
not mounted it yet (as on KDE, until a file manager opens it), and mounts it when you choose
it; when an MTP device is connected too, the command line opens the MTP device. `--path` is only for a folder without a device, such as a copy.
Once its drive is ejected, a watch in this mode switches to its Garmin USB mode, which
openExpress does not use: unplug the cable and plug it in again to use it again. Sync, apps, maps, music and backup are not
supported for such a device; the window and the commands say so (the window just has no
**Backup** row). A car or motorcycle
navigator that connects as a USB drive (such as an older nüvi) should therefore get its
software updates, but no map updates. Mass storage is tested by the developer on the Varia RTL515 (also
copying a component update and ejecting the drive in the window), and users
report software updates working on an inReach Mini 2, a Forerunner 55 and a Forerunner 920XT
(see the table); reports from other devices in mass storage mode are welcome. If you are not sure which mode your device
uses, plug it in: if it appears as a USB drive in your file manager rather than through MTP,
it is in mass storage mode.
