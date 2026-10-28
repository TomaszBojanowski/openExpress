Records of the last sync (last_sync.json) for tests/test_last_sync_patterns.py.
real-*: copies of records written by real syncs (copied 23.10.2026), the unit ID made up.
  real-source-edge-1050-0.6.15-download-only.json: sync --download-only --apply of the
  version from source after 0.6.15 (the shape of format-0.6.16-upload-skipped.json).
  real-source-fenix-8-0.6.18-trueup-received.json: a sync of the version from source after
  0.6.18 (with 6d8cd91, copied 24.10.2026): one new file uploaded, TrueUp received.
  real-flatpak-edge-1050-0.6.20-trueup-metrics.json: a sync of the Flatpak 0.6.20 (copied
  25.10.2026): eight new files uploaded, three metrics files and TrueUp from the queue; the
  files of the queue only counted, not named (the shape before 0.6.21).
format-*: the shapes older versions wrote, with made-up numbers; and shapes of this
  version no real sync has made yet (format-0.6.16-upload-skipped.json: sync --apply
  --download-only; format-0.6.16-only-types.json: sync --apply --type;
  format-0.6.19-trueup-received.json: a sync with nothing new that wrote TrueUp, counted as
  received; tests/test_fixes_ui.py checks all three are what the command writes;
  format-0.6.20-details-as-data.json: every line of the details kept as a kind and its
  numbers or names, put into words when shown (since 0.6.20; before, the lines of the
  first sync's choice, the tracker conflict, the Connect IQ apps, TrueUp and the items the
  device does not accept were kept as words of the moment of the sync) - a shape that
  gathers every kind, not one real sync; tests/test_last_sync_language.py checks it is
  what the steps of a sync write).
  format-0.6.21-received-files.json: the same sync as
  real-flatpak-edge-1050-0.6.20-trueup-metrics.json, as this version writes it: the files of
  the queue also as a line of their types (received_files), TrueUp and the Connect IQ apps
  not in it; tests/test_received_from_queue.py checks it is what a sync writes.
  format-0.7.5-ecg-not-uploaded.json: a sync of a device with one new monitoring file and
  one ECG recording, which is never uploaded (it needs a Garmin Health account): the line
  ecg_not_uploaded after the uploads; tests/test_ecg_not_uploaded.py checks it is what a
  sync writes. Synthetic: no real sync has made it yet (marked "synthetic" in it).
  format-0.7.10-stopped.json: a sync stopped after its uploads (Garmin Connect's queue did
  not answer): its record kept with the error that stopped it, as one of its errors (kept in
  the words of the moment of the sync, as every error); tests/test_stopped_sync.py checks it
  is what a sync writes. Synthetic: no real sync has made it yet (marked "synthetic" in it).
