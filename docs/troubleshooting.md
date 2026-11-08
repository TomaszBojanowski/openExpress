# Troubleshooting

- **The device stopped responding.** Over MTP a device's session can hang in the middle of a
  read, with the cable and USB fine. openExpress gives up after 30 s without an answer and says
  *The device stopped responding. Unplug it and plug it in again.* Do that: only unplugging the
  device has ended such a hang; until then openExpress, while it runs, asks nothing more of
  the device ([more](guide.md#a-device-that-stops-responding)).
- **The watch restarts by itself after it is unplugged.** A fēnix 8 has done this a few times
  while taking in a change, for example a new map or a removed Connect IQ app. No data on the
  watch was lost.
  - Before a bigger change (maps, several apps), it may help to turn the watch off and on first.
  - The watch keeps only the last such error in its log and overwrites it at the next restart,
    sometimes already when it is unplugged. To keep it, turn on Preferences → **Diagnostics** →
    **Keep Diagnostic Files** (what is copied and when: [Diagnostics](privacy.md#diagnostics)).
  - After such an error the device page shows a row **Device Error Log** (under **Backup**) for
    30 days, whether the copy is on or not.
- **The watch is not found after the computer slept.** Watches (seen on a fēnix 8 and a Venu
  2) lose their USB connection when the computer sleeps: unplug the cable and plug it in
  again.
- **An Edge 1050 restarts each time it is unplugged.** That is normal for this model; nothing
  needs to be done.
- **Device Not Supported.** The Garmin device connects neither over MTP nor as a USB drive
  that stays (an inReach Mini of the first generation connects with HID, and for its first
  minute with a small drive beside it): openExpress cannot use it. The window says so with
  the device's name and USB ID, and commands end with the same words (exit code 1). A report
  still works and says what is connected ([Reporting a problem](#reporting-a-problem)).
- **Garmin Connect shows a heat or altitude acclimation you did not expect.** With two
  devices on the account (for example a watch and an Edge), each keeps its own acclimation, and
  the daily values in Garmin Connect come from the device that reported last. Compare the
  acclimation on each device's own screen to see which one the value comes from.
- **"This device is not registered to your Garmin account", although you registered it.**
  Registering the product on garmin.com does not add it to Garmin Connect, whose list of
  devices openExpress checks. Add the device in Garmin Connect (pair it with the Garmin
  Connect app on your phone, or add it at connect.garmin.com), or here: **Add to Account** on
  the banner or on the device page, `openexpress register` on the command line
  (experimental); then sync again. If it is there already, check that the phone app and
  openExpress are signed in to the same account.

## KDE Plasma and Dolphin

Dolphin opens MTP devices through KDE's own MTP service (kmtpd from kio-extras, in the `kiod6`
process), not through gvfs. Once started, the service keeps every MTP device it has opened, also
after Dolphin is closed, and takes a device back as soon as its cable is plugged in again, so
gvfs cannot offer it; after 45 seconds the window says **Device Not Ready**. To free the device:

1. Close Dolphin.
2. Run `kquitapp6 kiod6`.
3. Unplug the cable and plug it in again.

Open the device in openExpress rather than in Dolphin; if you open it in Dolphin again, repeat
these steps.

## Signing in

Signing in needs the Garmin Express installer for Windows first: openExpress signs in with
the application key of Garmin Express, which it reads from the installer. No key or API
access needs to be created in Garmin Connect. Until the installer is chosen, Preferences
offers **Choose Installer…** instead of **Sign In**, and `openexpress login` says to run
`openexpress setup --from-installer /path/to/GarminExpress.exe`.

If the login window does not capture the ticket, use `openexpress login --manual`: you
sign in using a regular browser and paste the final URL containing `ticket=ST-…`.

## Log file

openExpress keeps a log of what it does, to make problems easier to find:

- **Where:** `~/.local/state/openexpress/openexpress.log`; in the Flatpak
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/.local/state/openexpress/openexpress.log`.
  The command-line tool, the window and the background sync write to the same file. At most
  three files of about 1 MB are kept; the file is readable only by you. Next to it,
  `crash.log` keeps Python's trace of a crash of the window; its end goes with the log into a
  report.
- **Telling runs apart:** every line carries a short random marker (e.g. `cli[a3f1]`) that is
  the same for one run of the program and means nothing else. The first line of a run says
  the command, the version, whether it runs from source or is the Flatpak (with the
  repository it was installed from and its commit), the Python version, the language of the
  app, the locale of the C library and the process ID; nothing about you or your computer.
- **What it contains:**
  - which command or sync ran and how it ended;
  - the steps of a sync (numbers of files by type, not their contents);
  - app installs and removals;
  - which of the software updates offered were chosen and which not, and why some were left out
    (by part number and version);
  - how the device was detected (including when it was mounted or unmounted);
  - requests to Garmin's servers as method, address without parameters, status and duration;
  - error messages;
  - of each dialog of the window (a confirmation dialog or a message): its heading, its words in
    one line and the answer; of those about your own files, maps, music or courses, only which
    one it was and the answer.
- **What it never contains:** serial numbers, Unit IDs, MTP addresses, tokens, passwords,
  cookies, e-mail addresses, local paths, addresses of local files and network shares
  (`file://`, `smb://`, `sftp://`…), your user name, names of your music files, or file
  contents. Every line passes a filter that replaces such values before it is written. The log
  stays on your computer; nothing is sent anywhere.
- **More or less:** `OPENEXPRESS_LOG=debug` (or `-v`) writes more detail,
  `OPENEXPRESS_LOG=off` writes no file. What `-v` and the background sync print on the
  terminal passes the same filter; messages meant for you (e.g. the addresses to choose
  from with `--device-uri`) and the output of commands such as `openexpress info` do not.
- **In a bug report:**
  - On the command line, `openexpress report` says where the log is; `openexpress report --log`
    prints its last 200 lines instead, filtered once more, ready to paste into the form's “Log”
    field or into a comment (the address it prints fills in the report).
  - In the window, tick **Attach the last log** in **Report a Device or Problem…**; the preview
    shows the report with the log, as **Copy** copies it. **Open on GitHub** puts only the
    report into the form and the same log lines into the clipboard, to paste into the form's
    “Log” field or into a comment: the log never goes into the address.
  - The log comes between two `~~~` lines, so that GitHub shows it as code in a comment too.
    Read it before you send it.

## The Flatpak and the language of your system

The Flatpak runtime has the locale data only of the languages Flatpak installed for it (see
`flatpak config`). If your system's language is not one of them, openExpress switches itself
to the `C.UTF-8` locale, so that file names and the sign-in window work as usual, and the
log says so in one line. openExpress's own texts are not affected; GTK's own (the buttons of
its dialogs and the like) are then in English. To have those in your language as well, add
it and update (with the code of your language instead of `da`):

```bash
flatpak config --user --set extra-languages da
flatpak update
```

## Reporting a problem

A [device report](https://github.com/TomaszBojanowski/openExpress/issues/new?template=device_report.yml)
says how openExpress works with your device, also when everything works. In the window: main
menu (☰) → **Report a Device or Problem…** → **Open on GitHub** opens the form with the report
and a title filled in; or run `openexpress report`, which prints the report and an address that
does the same. The report contains no serial number, Unit ID or e-mail. Nothing is published
until you submit the form.

When no device can be opened (one not ready or not supported, none connected), the window and
`openexpress report` still give a report: why no device is open, the Garmin devices on USB (their USB ID, the name each
gives itself and the kinds of its interfaces) and whether gvfs has MTP, then the system;
`openexpress report --log` gives the log as always.

For a bug, send the device report with the command you ran and, if something failed, the
output of `openexpress report --log` (see [Log file](#log-file)). Never post:

- the output of `openexpress info`: it shows the Unit ID;
- the files in `~/.config/openexpress/` (Flatpak:
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/config/openexpress/`): `connect_auth.json`
  is your login token, and `consumer.json` holds what setup read from the Garmin Express
  installer (the application key, its secret and the value that signs download addresses);
- your `GarminDevice.xml`: it contains the Unit ID, the serial number and map unlock codes.
