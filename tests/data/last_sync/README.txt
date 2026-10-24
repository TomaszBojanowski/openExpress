Records of the last sync (last_sync.json) for tests/test_last_sync_patterns.py.
real-*: copies of records written by real syncs (copied 23.10.2026), the unit ID made up.
  real-source-edge-1050-0.6.15-download-only.json: sync --download-only --apply of the
  version from source after 0.6.15 (the shape of format-0.6.16-upload-skipped.json).
format-*: the shapes older versions wrote, with made-up numbers; and shapes of this
  version no real sync has made yet (format-0.6.16-upload-skipped.json: sync --apply
  --download-only; format-0.6.16-only-types.json: sync --apply --type;
  format-0.6.19-trueup-received.json: a sync with nothing new that wrote TrueUp, counted as
  received; tests/test_fixes_ui.py checks all three are what the command writes).
