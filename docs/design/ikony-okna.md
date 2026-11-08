# The window's icons

What each place of the window shows as its icon, and why.
The icons of the device kinds on the
start page's tiles stay as they are.

## How an icon is chosen

- An icon of the GNOME theme (Adwaita) where one says the thing; else a shape of GNOME's
  icon-development-kit (CC0 1.0), redrawn; else a drawing of our own.
- Our own and the kit's are drawn for the 16 px grid: 2 px lines, round ends and joins, the
  shapes from edge to edge, monochrome (the theme's colour), in `packaging/icons/` and made into
  shapes by `tools/symbolic-icons.py`.
- Two or three ideas for each place, then a blind test: a reviewer who knows only what the
  program is for names each icon from a shuffled sheet at 16 and 32 px. An icon read wrongly, or
  confused with another icon of the window, is dropped.
- One icon for one meaning in the window: an icon already used for something else (the pin of
  Maps, the grid of the start page's view) is not taken for another place.
- Garmin's icons are never copied or imitated.

## What changed

| Place | Before | Now | Why |
|---|---|---|---|
| Side panel: Device | a clock | the small icon of the open device's kind (watch, cycling computer, handheld, bike accessory; any other: a neutral device), the neutral one until the kind is known | a clock suits a watch only; the tile's kind icons are drawn for a large size and come out too thin at 16 px, so the kinds are drawn again for 16 px |
| Side panel, empty list, page without a summary: Activities | a calendar | a running figure | a calendar says "dates"; the runner was the one idea read as "workouts" |
| Tile: Distance | the pin | two points and the line between them | the pin is Maps, and a place, not a length |
| Tile: Time | a clock | the stopwatch (the kit) | a duration |
| Tiles: Pace, Speed | the theme's filled gauge | a speedometer (the kit) | the same meaning, drawn as the other tiles |
| Tiles: Heart rate | the theme's emoji heart | the heart (the kit) | the same meaning, drawn as the other tiles |
| Tile: Calories | knife and fork | a flame | food is not calories burned |
| Tile: Ascent | an arrow up | two peaks | the same meaning as height, drawn as the other tiles |

The cycling computer's small icon stands on a handlebar: drawn alone, it read as a phone.

## What stays

The header bar (main menu, refresh, Sync with the arrows of sending and receiving: arrows in a
circle would be confused with Refresh next to it), the start page's view buttons, the side
panel's Updates, Maps, Apps and Music, warnings and errors, the arrows of rows, the update
page's large icon, the Maps page without maps, the placeholder of an app's icon, Export as GPX
(a file is saved), the chosen lap, zoom, Preferences and the trash buttons.
