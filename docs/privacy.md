# Privacy and security

- The password is entered only on Garmin's page; openExpress never sees or stores it. What
  is stored locally after signing in is an OAuth1 token and, next to it, your user name,
  e-mail address, profile number and account identifier, in one file readable only by you
  (permissions 0600): `~/.config/openexpress/connect_auth.json`, or in the Flatpak
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/config/openexpress/connect_auth.json`.
  `openexpress logout` removes that file.
- Besides the login, this installation keeps what it knows of each device in
  `~/.local/share/openexpress` (Flatpak:
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/data/openexpress`), readable only by
  you: the devices it has seen (name, part number, unit ID, software version, what the last
  update check found), the names of the files already uploaded, the last sync, copies of
  files whose upload failed until they are uploaded, and the list of your own maps.
  **Forget Device…** removes what it keeps of a device.
- **No telemetry.** Unlike Garmin Express, the program sends nothing to
  Microsoft Application Insights. Watch diagnostics (error logs, KPIs, memory
  dumps, behaviour log) are not uploaded without `--include-diagnostics`.
- **The device's diagnostic files stay on this computer.** Once per connection, when a device
  is opened, the window reads its error logs (`Garmin/Debug/err_log.*`) to show a recent error; of each entry
  only the kind, the time, the error number, where the software stopped and the screen are
  used, never the unit ID or the backtrace in it. Where it found them and the device's other
  diagnostic files is kept (`diagnostic-places.json`: the paths, the device's software version
  and whether its event logs are visible, nothing of the files themselves, in the device's
  folder named below), so that the next openings read them there without looking through the
  device's folders; when one is gone, or the device has other software, it looks through
  them again (what was not there is looked for again only then). With **Preferences →
  Diagnostics** turned on
  (off by default; `openexpress setup --diagnostics-copy on`), the window also copies, once
  per connection and through the ordinary connection, the diagnostic files the device shows:
  `Garmin/Debug`, the folders of its diagnostic data types and its event logs. Health
  metrics (`MetricsBackup`), settings (`Backup`), memory dumps and files of more than 4 MB are
  only listed. The hidden crash logs of a fēnix 8 (its boot log and event logs) are read only
  when you ask (**Copy Crash Logs**, `openexpress diagnostics --crash-logs --apply`), in a
  session of openExpress's own, and the memory dumps (encrypted by Garmin) are copied only
  with **Copy Memory Dumps** or `openexpress diagnostics --dumps --apply`. The copies are in the device's folder of the data
  of openExpress (`~/.local/share/openexpress/devices/`, in the Flatpak under
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/data/openexpress/devices/`), readable
  only by you; at most 30 copies and 50 MB per device, the oldest removed first. Forgetting
  the device removes them. These copies are never sent (`sync --include-diagnostics`
  uploads the device's diagnostic files from the device itself).
- Sync **never deletes files from the watch**. Files are deleted only when you ask for it:
  by `maps install/update/remove` (old map files indicated by the server), `maps remove
  --own`, `music remove` and `apps remove`; on the command line only with `--apply` (without
  it, a dry run lists them). Before writing a software update, openExpress deletes a file of
  the same name in another letter case and, for a system update, a leftover `GUPDATE.TMP` in
  its folder; it removes a copy that failed its check. Files
  shared by several maps (e.g. the Europe terrain model) are protected. When Garmin's map
  service asks for old files to be cleaned up (`directoriesToRemove`), the question before a
  map is installed, updated or removed and `openexpress maps install/update/remove` list the
  files that would match (whole names, never a map of the answer or a map of your own);
  openExpress does not remove them yet.
- A write to the device that fails leaves no half-written file: what was written of it is
  deleted, and when the copy did not start, the file that was there stays. On a device in
  mass storage mode the new file is written beside the old one and only then takes its
  place. The list of apps waiting for the device (`IN.BIN`) is put back as it was, or, if
  even that fails, deleted, and the message says that the changes are lost.
- Software updates and maps: downloaded files are verified (size, MD5);
  they are copied to the watch only after all files have been downloaded. The RGN format
  (older devices) is rejected.
- Upload works as in Express: activities that Connect already has (e.g. from the phone) are
  detected and skipped; duplicates are rejected by the server.
- Nothing is uploaded unless the Garmin account has given the consent to upload data from
  devices and has device sync turned on; both are checked before every sync. Files from
  Garmin Connect are still written to the device. A data type one of whose upload
  locations needs a further consent is not uploaded at all, to any of its locations, until
  that consent is given (as in Garmin Express).
- The picture of a device that is not on the signed-in account (or when nobody is signed
  in) is looked up on Garmin's update server, and so, once each time openExpress runs, is
  what that server says of an open device in mass storage mode whose kind Garmin has not
  told. The same request tells which map service serves the device: it is made once each
  time a device is opened, and again when its maps are checked again by hand, and its
  answer also serves the picture. Like the update check, that request names the device
  (unit ID). The answer is kept in memory only while the device is open. Of it, only the
  picture is cached, and three things are kept with the devices this installation
  remembers: the address of the model's picture (so that the picture can be downloaded
  again after the cache is cleared), Garmin's name of the model when the device gives it in
  capitals only, and whether the device is a bike accessory. Its market segment also tells
  whether an update erases the device's data. Of the answer, the log names only the map update type (which
  map service Garmin uses for the model) and, for a device whose kind Garmin does not tell
  otherwise, the market segment and the connect support – always with the part number
  only – as it names the account's categories of such a device. When that server gives no
  picture of a device that is not on your account, its page offers **Get Picture From
  Account**: only that click asks your Garmin Connect account for the picture, and that
  request sends the device's unit ID to your account. Nothing else asks the account about a
  device that is not on it.
- openExpress identifies itself to Garmin's servers: every request carries the header
  `User-Agent: openexpress/<version> (Linux)`; the sign-in window is a web browser with its
  own. It uses the Garmin Express client headers so that the servers answer, but it does not
  hide that it is a different program.
- The `Garmin-Client-*` headers have the values of Garmin Express 7.29.1.0 for Windows (name,
  version, platform and its version), with a random session ID and the language of the
  program. The request for the upload configuration also carries a random ID made once for
  this installation (`client_guid` in `settings.json`). The values can be changed in
  `~/.config/openexpress/settings.json` (Flatpak:
  `~/.var/app/io.github.tomaszbojanowski.OpenExpress/config/openexpress/settings.json`).

## Flatpak permissions

Besides network, the usual permissions of a window (Wayland, X11 as a fallback, the GPU),
access to devices through GNOME's gvfs (`--talk-name=org.gtk.vfs.*`, `xdg-run/gvfsd`), your Downloads
and Music folders read-only (`xdg-download:ro`, `xdg-music:ro`: the installer and music on the
command line) and the folder Documents/Garmin for backups (`xdg-documents/Garmin:create`,
created if missing), the Flatpak has three permissions that weaken the sandbox:

- It can run commands on the host (`org.freedesktop.Flatpak`). It uses this to copy files
  to the watch with `gio copy`, because gvfs does not let sandboxed apps write to MTP
  devices, and to read which repository the Flatpak was installed from
  (`flatpak info --show-origin`, for the device report and the log; it changes nothing).
- It can open USB devices (`--device=usb`: the nodes in `/dev/bus/usb`, nothing else of
  `/dev`). It uses this only for two things that need a direct USB connection: Connect IQ
  apps on devices that hide their app folder (e.g. the fēnix 8: installing, updating and
  removing them), and reading the hidden crash logs of such a device when you ask. The
  device disappears from the file manager for a moment while it is used.
- It can read and write the drives the system mounts under `/run/media` and `/media`
  (`--filesystem=/run/media`, `--filesystem=/media`): every removable drive mounted there
  (USB sticks, memory cards, external disks), not only Garmin devices. It uses this only for
  Garmin devices in mass storage mode (e.g. the Varia RTL515), which connect as a drive.

If you prefer not to grant the first one, use the [version from source](install.md#from-source)
(writing to the watch then works without it). The second one can be turned off with
`flatpak override --user --nodevice=usb io.github.tomaszbojanowski.OpenExpress`; a Flatpak
older than 1.16 does not know this permission and runs as without it. Without it, on devices that hide their app folder, installs and
updates are only queued in Garmin Connect, from where the phone or Garmin Express installs
them, apps are removed on the phone, nothing is written to the device, and the hidden crash logs
cannot be read. The third one can
be turned off with `flatpak override --user --nofilesystem=/run/media --nofilesystem=/media
io.github.tomaszbojanowski.OpenExpress`; the window then says that it has no access to the
drive, and devices in mass storage mode are left to the version from source.
