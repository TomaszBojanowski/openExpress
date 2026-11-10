#!/usr/bin/env python3
"""The page Device Status checked through its states in one command: tests/test_status_review.py
in the real window (made-up devices, 360 px and wide, English, Polish and German), with a picture
of every state written to OUT_DIR and an index.html to look at them.

Usage: tools/status-review.py OUT_DIR

The exit code is the tests': 0 when every state holds what the review asks of it. Nothing is read
from a device, the network or the program's folders (the tests run in folders of their own).
"""

import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = re.compile(r"(?P<shape>[a-z]+)-(?P<lang>[a-z]{2})-(?P<width>narrow|wide)-(?P<n>\d+)-"
                  r"(?P<state>[a-z-]+)\.png")


def index(folder: Path, result: int, summary: str) -> None:
    pictures = [(m.groupdict(), p.name) for p in sorted(folder.glob("*.png"))
                if (m := NAME.fullmatch(p.name))]
    groups: dict[tuple, list] = {}
    for found, name in pictures:
        groups.setdefault((found["shape"], found["lang"], found["width"]), []).append(
            (int(found["n"]), found["state"], name))
    sections = []
    for (shape, lang, width), shots in groups.items():
        figures = "".join(
            f'<figure class="{width}"><a href="{html.escape(name)}"><img src="{html.escape(name)}"'
            f' loading="lazy"></a><figcaption>{n:02d} · {html.escape(state)}</figcaption></figure>'
            for n, state, name in sorted(shots))
        sections.append(f"<section><h2>{shape} · {lang} · {width}</h2>"
                        f"<div class=\"row\">{figures}</div></section>")
    verdict = "passed" if result == 0 else f"FAILED (exit code {result})"
    (folder / "index.html").write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Device Status review</title>
<style>
:root {{ --bg:#fafafa; --fg:#1e1e1e; --dim:#6b6b6b; --line:#ddd; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#1d1d20; --fg:#eee; --dim:#9a9a9a; --line:#333; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 sans-serif; margin:0;
  padding:24px 16px; }}
main {{ max-width:1800px; margin:0 auto; }}
h1 {{ font-size:22px; margin:0 0 4px; }} pre {{ color:var(--dim); white-space:pre-wrap; }}
h2 {{ font-size:15px; margin:24px 0 8px; border-top:1px solid var(--line); padding-top:12px; }}
.row {{ display:flex; flex-wrap:wrap; gap:12px; align-items:flex-start; }}
figure {{ margin:0; }} figure.wide {{ width:min(100%, 420px); }}
figure.narrow {{ width:min(100%, 200px); }}
img {{ width:100%; height:auto; display:block; border:1px solid var(--line); }}
figcaption {{ color:var(--dim); font-size:13px; margin-top:4px; }}
</style></head><body><main>
<h1>Device Status review: {verdict}</h1>
<pre>{html.escape(summary)}</pre>
{"".join(sections)}
</main></body></html>
""")


def main() -> int:
    if len(sys.argv) != 2:
        print(next(line for line in __doc__.splitlines() if line.startswith("Usage:")),
              file=sys.stderr)
        return 2
    folder = Path(sys.argv[1]).expanduser().resolve()
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    env = dict(os.environ, OPENEXPRESS_REVIEW_SHOTS=str(folder))
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "tests/test_status_review.py"], cwd=ROOT, env=env,
                         capture_output=True, text=True, check=False)
    lines = run.stdout.strip().splitlines()
    failures = [line for line in lines if line.startswith(("FAILED", "E   "))]
    summary = "\n".join([*failures[:40], lines[-1] if lines else ""])
    index(folder, run.returncode, summary)
    print(summary)
    print(f"{len(list(folder.glob('*.png')))} pictures: {folder / 'index.html'}")
    return run.returncode


if __name__ == "__main__":
    sys.exit(main())
