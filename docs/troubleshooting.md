# Troubleshooting

- **The watch restarts by itself after it is unplugged.** A fēnix 8 (software 23.39) has done
  this a few times while taking in a change, for example a new map or a removed Connect IQ
  app; the same change went through without a restart right after the watch had been turned
  off and on. The watch also kept memory dumps of crashes or restarts from 2025 and April 2026,
  before openExpress was first used with it. This looks like a fault of the watch that appears
  after it has been running for days, but it is not certain. Before a bigger change (maps,
  several apps) it may help to turn the watch off and on first. No data on the watch was lost.
  The watch keeps only the last such error in its log and overwrites it at the next restart,
  sometimes already when it is unplugged, so to keep it, turn on **Preferences → Diagnostics
  → Keep a copy of the device's diagnostic files on this computer**: the window then copies
  them each time it opens the watch after it was connected, before anything is written to it. After such an error the device page shows a row **Device
  error log** (under **Backup**) for 30 days, whether the copy is on or not.
- **The watch is not found after the computer slept.** Watches (seen on a fēnix 8 and a Venu
  2) lose their USB connection when the computer sleeps: unplug the cable and plug it in
  again.
- **An Edge 1050 restarts each time it is unplugged.** It always does.

## Signing in

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
- **What it contains:** which command or sync ran and how it ended, the steps of a sync
  (numbers of files by type, not their contents), app installs and removals, how the device
  was detected (including when it was mounted or unmounted), requests to Garmin's servers
  as method, address without parameters, status and duration, and error messages.
- **What it never contains:** serial numbers, unit IDs, MTP addresses, tokens, passwords,
  cookies, e-mail addresses, local paths, addresses of local files and network shares
  (`file://`, `smb://`, `sftp://`…), your user name, names of your music files, or file
  contents.
  Every line passes a filter that replaces such values before it is written. The log
  stays on your computer; nothing is sent anywhere.
- **More or less:** `OPENEXPRESS_LOG=debug` (or `-v`) writes more detail,
  `OPENEXPRESS_LOG=off` writes no file. What `-v` and the background sync print on the
  terminal passes the same filter; messages meant for you (e.g. the addresses to choose
  from with `--device-uri`) and the output of commands such as `openexpress info` do not.
- **In a bug report:** `openexpress report` says where the log is; `openexpress report --log`
  prints its last 200 lines instead, filtered once more, ready to paste into the form's
  “Log” field or into a comment (the address it prints fills in the report). In the window,
  tick **Attach the last log** in *Report a Device or Problem…*; the preview shows the report
  with the log, as **Copy** copies it. **Open on GitHub** puts only the report into the form
  and the same log lines into the clipboard, to paste into the form's “Log” field or into a comment: the log never
  goes into the address. The log comes between two `~~~` lines, so that GitHub shows it as
  code in a comment too. Read it before you send it.

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

Bug reports: please send the device report (main menu ☰ → **Report a Device or Problem…**, or
`openexpress report`; it has no serial number, unit ID or e-mail), the command you ran and,
if something failed, the output of `openexpress report --log`. Do not post the output of
`openexpress info`: it shows the unit ID. Never post the files in `~/.config/openexpress/`
(Flatpak: `~/.var/app/io.github.tomaszbojanowski.OpenExpress/config/openexpress/`): `connect_auth.json` is
your login token, and `consumer.json` holds what setup read from the Garmin Express
installer (the application key, its secret and the value that signs download addresses).
Do not post your `GarminDevice.xml` either (it contains the unit ID, the serial number and
map unlock codes).
