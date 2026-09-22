"""Generate fake export files so every step can be tested before real data exists.

Writes an iMazing-shaped CSV and an SMS Backup & Restore-shaped XML containing
the same synthetic conversation: outgoing messages with deliberate style quirks,
incoming messages that must be filtered out, and low-signal chatter that must be
dropped.

Usage: style-ft dummy-export --outdir data/dummy
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

ME = "Me"
THEM = "Sam Rivera"

# (direction, text) - direction "out" is mine.
CONVERSATION: list[tuple[str, str]] = [
    ("in", "hey are we still on for saturday?"),
    ("out", "yeah ofc, was gonna text u about it — thinking we grab food around 7 then head over? i can drive if parking is gonna be a nightmare again"),
    ("in", "works for me"),
    ("out", "bet"),
    ("out", "ok so i looked at the menu and it's kinda pricey lol, but the reviews are unreal so i think it's worth it one time. lmk if u wanna do the cheaper spot instead, genuinely do not care"),
    ("in", "let's do the nice one"),
    ("out", "perfect, booked it. 7:15 was the only slot left so we're slightly rushing but should be fine"),
    ("in", "how was the flight"),
    ("out", "honestly brutal. sat next to a guy who watched three movies at full volume with no headphones and the flight attendant just... let it happen. anyway i'm alive, landed like an hour ago, currently horizontal"),
    ("out", "lol"),
    ("out", "gonna crash early tonight but tmrw i'm free after like 2 if u wanna get coffee, there's that place by the park i keep meaning to try"),
    ("in", "sounds good"),
    ("out", "quick heads up — the deploy went sideways this morning so i'm probably heads down till late. not ignoring u, just buried. will ping when i surface"),
    ("out", "ok update: fixed. turned out to be a config thing that took 4 hours to find and 30 seconds to change, which is the most on-brand outcome possible"),
    ("in", "classic"),
    ("out", "idk man i think i'm just gonna skip the gym today, body feels like it got hit by a truck and pushing through that never ends well for me"),
    ("out", "omw"),
    ("out", "so the apartment thing fell through 🙃 landlord decided to 'reassess' two days before signing. i'm annoyed but honestly the commute was rough anyway so maybe it's fine"),
    ("in", "that sucks, sorry"),
    ("out", "all good, already found two other places to look at this weekend. one of them is smaller but the light is insane and i think i care about that more than square footage at this point"),
    ("out", "did u ever end up watching that show i sent? no pressure just curious bc i finished it and have nobody to talk about the ending with"),
    ("in", "not yet!"),
    ("out", "ok no spoilers then, but when u get there text me immediately because i need someone to be normal about it with me"),
    ("out", "thx"),
    ("out", "running like 10 min behind, hit every light on the way. order me whatever you're getting, i trust u completely and also do not want to make a decision right now"),
    ("in", "ok"),
    ("out", "that was so good. genuinely one of the better meals i've had this year and i'm not even being dramatic. we're going back"),
    ("out", "sry just saw this, phone was on dnd all afternoon. yeah tuesday works, i'll put it in the calendar so i don't forget like last time"),
    ("in", "haha"),
    ("out", "real talk though, i think you should just go for it. worst case it doesn't work and you're exactly where you are now, which is a pretty low downside for something you've been thinking about for like a year"),
    ("out", "😂😂"),
    ("out", "im gonna be afk for a bit, got a thing. back in ~2 hrs"),
    ("out", "ok so here's my take on the whole situation: everyone's overthinking it. the plan is fine, the timeline is fine, the only actual problem is that nobody wants to be the one to say we're starting monday. so i'm saying it. we start monday"),
    ("in", "lol ok"),
    ("out", "https://example.com/some-article"),
    ("out", "wait actually read that ^ it's short but it explains the thing i was badly describing at dinner way better than i did"),
    ("out", "k"),
    ("out", "birthday plans update: doing something small. like 6 people, my place, probably too much food. u in? bring nothing, i mean it, last time somebody brought a cheese plate and i'm still recovering emotionally"),
    ("in", "obviously in"),
    ("out", "i keep going back and forth on the job thing. the money is better but the team seems like a mess and i've been the person who takes the money and regrets it before. leaning toward staying but ask me again in a week"),
]


def _rows() -> list[dict[str, str]]:
    rows = []
    base = 1_700_000_000_000  # epoch millis
    for i, (direction, text) in enumerate(CONVERSATION):
        rows.append(
            {
                "direction": direction,
                "text": text,
                "epoch_ms": str(base + i * 600_000),
                "sender": ME if direction == "out" else THEM,
            }
        )
    return rows


def write_csv(path: Path) -> int:
    rows = _rows()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["Chat Session", "Message Date", "Service", "Type", "Sender Name", "Text"]
        )
        for r in rows:
            writer.writerow(
                [
                    f"{THEM} (iMessage)",
                    r["epoch_ms"],
                    "iMessage",
                    "Outgoing" if r["direction"] == "out" else "Incoming",
                    r["sender"],
                    r["text"],
                ]
            )
    return len(rows)


def write_xml(path: Path) -> int:
    rows = _rows()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<smses count="{len(rows)}">']
    for r in rows:
        lines.append(
            "  <sms address={addr} date={date} type={type} body={body} "
            "contact_name={name} />".format(
                addr=quoteattr("+15551234567"),
                date=quoteattr(r["epoch_ms"]),
                type=quoteattr("2" if r["direction"] == "out" else "1"),
                body=quoteattr(r["text"]),
                name=quoteattr(r["sender"]),
            )
        )
    lines.append("</smses>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", default="data/dummy")
    args = ap.parse_args(argv)

    outdir = Path(args.outdir)
    n_csv = write_csv(outdir / "export.csv")
    n_xml = write_xml(outdir / "export.xml")
    print(f"wrote {n_csv} rows -> {outdir / 'export.csv'}")
    print(f"wrote {n_xml} rows -> {outdir / 'export.xml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
