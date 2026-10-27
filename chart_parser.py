import re

SLIDE_SHAPES = ['pp', 'qq', '-', '^', '<', '>', 'v', 'V', 'w', 's', 'z', 'p', 'q']
SHAPE_ALT = '|'.join(re.escape(s) for s in SLIDE_SHAPES)

NOTE_PATTERN = re.compile(r'([A-E]?\d)([bx]*)(h)?(?:\[(\d+):(\d+)\])?')

SLIDE_HEAD = re.compile(rf'^([1-8])([bx]*)((?:{SHAPE_ALT}).*)$')
SLIDE_SEG = re.compile(rf'({SHAPE_ALT})([1-8]+)([bx]*)(?:\[(\d+):(\d+)\])?')


def duration_ms(bpm, divider, multiplier):
    return multiplier * (60000 / bpm) * (4 / divider)


def _slide_length(bpm, div_str, mult_str):
    if div_str and mult_str:
        return duration_ms(bpm, int(div_str), int(mult_str))
    return duration_ms(bpm, 4, 1)


def parse_slide(sub_token, current_time_ms, bpm):
    head = SLIDE_HEAD.match(sub_token)
    if not head:
        return None

    star, markers, body = head.groups()

    paths = []
    length = None
    for path in body.split('*'):
        waypoints = [star]
        shapes = []
        for shape, digits, _markers, div_str, mult_str in SLIDE_SEG.findall(path):
            for d in digits:
                waypoints.append(d)
                shapes.append(shape)
            if div_str:
                length = _slide_length(bpm, div_str, mult_str)
        if len(waypoints) >= 2:
            paths.append((waypoints, shapes))

    if not paths:
        return None
    if length is None:
        length = _slide_length(bpm, None, None)

    events = []
    for waypoints, shapes in paths:
        events.append({
            "time_ms": round(current_time_ms),
            "type": "SLIDE",
            "start": waypoints[0],
            "end": waypoints[-1],
            "shape": shapes[0],
            "waypoints": waypoints,
            "shapes": shapes,
            "duration_ms": round(length),
            "break": "b" in markers,
            "ex": "x" in markers,
        })
    return events


def parse_sub_token(sub_token, current_time_ms, bpm):
    slide_events = parse_slide(sub_token, current_time_ms, bpm)
    if slide_events is not None:
        return slide_events

    events = []
    for match in NOTE_PATTERN.finditer(sub_token):
        pos, note_markers, is_hold, div_str, mult_str = match.groups()
        is_touch = pos[0] in "ABCDE"
        flags = {"break": "b" in note_markers, "ex": "x" in note_markers}

        if is_hold:
            length = _slide_length(bpm, div_str, mult_str)
            events.append({
                "time_ms": round(current_time_ms),
                "type": "TOUCH_HOLD" if is_touch else "HOLD",
                "pos": pos,
                "duration_ms": round(length),
                **flags,
            })
        else:
            events.append({
                "time_ms": round(current_time_ms),
                "type": "TOUCH" if is_touch else "TAP",
                "pos": pos,
                **flags,
            })
    return events


def parse_chart(chart_str, bpm=120, default_divisor=4):
    events = []
    current_time_ms = 0.0
    divisor = default_divisor

    for token in chart_str.strip().split(','):
        token = token.strip()

        while token[:1] in ('(', '{'):
            close = ')' if token[0] == '(' else '}'
            end = token.index(close)
            value = float(token[1:end])
            if token[0] == '(':
                bpm = value
            else:
                divisor = value
            token = token[end + 1:]

        for sub_token in token.split('/'):
            events.extend(parse_sub_token(sub_token, current_time_ms, bpm))

        current_time_ms += (60000 / bpm) * (4 / divisor)

    return events


if __name__ == "__main__":
    chart = ("(185){8}1,,7xh[8:3],,8bx,,1-7-5-3[2:1],,2V46[2:1],,"
             "6p5>7[8:6],,4-8b*-2b[8:1],,C1h[4:1],")
    for e in parse_chart(chart):
        print(e)
