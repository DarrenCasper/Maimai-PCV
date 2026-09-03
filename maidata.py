import re
from pathlib import Path

from chart_parser import parse_chart

# simai difficulty slots. maidata files use &inote_2 .. &inote_7 with
# &lv_2 .. &lv_7 giving the displayed level. 2=Basic ... 6=Re:Master.
DIFFICULTY_NAMES = {
    1: "Easy", 2: "Basic", 3: "Advanced", 4: "Expert",
    5: "Master", 6: "Re:Master", 7: "Utage",
}


def load_maidata(path):
    """Read a maidata.txt into a dict of its &key=value fields.

    A value runs from after the '=' until the next line that starts with '&'
    (or EOF), so the multi-line inote_* chart bodies come through intact.
    """
    text = Path(path).read_text(encoding="utf-8")
    fields = {}
    for m in re.finditer(r"&([a-z0-9_]+)=(.*?)(?=\n&|\Z)", text, re.S):
        fields[m.group(1)] = m.group(2).strip()
    return fields


def list_charts(fields):
    """Which difficulties this file actually contains: {slot: (name, level)}."""
    out = {}
    for key in fields:
        m = re.fullmatch(r"inote_(\d)", key)
        if m and fields[key].strip():
            slot = int(m.group(1))
            out[slot] = (DIFFICULTY_NAMES.get(slot, f"Slot {slot}"),
                         fields.get(f"lv_{slot}", "?"))
    return out


def _clean_body(body):
    """Chart body -> one whitespace-free token stream, trailing 'E' removed."""
    body = re.sub(r"\s+", "", body.strip())
    if body.endswith("E"):
        body = body[:-1]
    return body


def get_events(fields, slot):
    """Parsed note events for one difficulty slot (e.g. 4 = Expert).

    Times are shifted by &first (seconds of lead-in before the chart's
    beat 1) so they line up with the audio clock.
    """
    key = f"inote_{slot}"
    if key not in fields:
        raise KeyError(f"{key} not in this maidata (have: {sorted(fields)})")

    events = parse_chart(_clean_body(fields[key]))

    first_ms = round(float(fields.get("first", 0)) * 1000)
    if first_ms:
        for e in events:
            e["time_ms"] += first_ms
    return events


if __name__ == "__main__":
    import sys

    md_path = sys.argv[1] if len(sys.argv) > 1 else "levels/メズマライザー/maidata.txt"
    fields = load_maidata(md_path)

    print(f"title : {fields.get('title')}")
    print(f"artist: {fields.get('artist')}")
    print(f"first : {fields.get('first')} s")
    print("charts:")
    for slot, (name, lv) in sorted(list_charts(fields).items()):
        events = get_events(fields, slot)
        types = {}
        for e in events:
            types[e["type"]] = types.get(e["type"], 0) + 1
        last = max((e["time_ms"] for e in events), default=0)
        print(f"  slot {slot} {name:<10} Lv{lv:<3}  {len(events):>4} events, "
              f"ends ~{last/1000:.1f}s   {types}")
