import re

# One note, as a pattern: a digit (position), an optional "h" (hold marker),
# then an optional [divider:multiplier] duration bracket.
NOTE_PATTERN = re.compile(r'(\d)(h)?(?:\[(\d+):(\d+)\])?')


def duration_ms(bpm, divider, multiplier):
    """Same 'length of one slot' formula as before, reused for hold length."""
    return multiplier * (60000 / bpm) * (4 / divider)


def parse_chart(chart_str, bpm=120, default_divisor=4):
    events = []
    current_time_ms = 0
    divisor = default_divisor

    tokens = chart_str.strip().split(',')

    for token in tokens:
        token = token.strip()

        if token.startswith('{'):
            end = token.index('}')
            divisor = int(token[1:end])
            token = token[end + 1:]

        for match in NOTE_PATTERN.finditer(token):
            pos, is_hold, div_str, mult_str = match.groups()

            if is_hold:
                length = duration_ms(bpm, int(div_str), int(mult_str))
                events.append({
                    "time_ms": round(current_time_ms),
                    "type": "HOLD",
                    "pos": pos,
                    "duration_ms": round(length),
                })
            else:
                events.append({
                    "time_ms": round(current_time_ms),
                    "type": "TAP",
                    "pos": pos,
                })

        slot_duration_ms = (60000 / bpm) * (4 / divisor)
        current_time_ms += slot_duration_ms

    return events


if __name__ == "__main__":
    chart = "1,,2,,{8}1,3,5,7,,,4h[4:1],,"
    for e in parse_chart(chart, bpm=120):
        print(e)