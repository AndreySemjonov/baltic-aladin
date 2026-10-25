#!/usr/bin/env python3
"""Read Kolka wind data from wind.png and optionally upload it to Windguru.

The image uses a fixed 5x8 bitmap font.  This script intentionally uses only
the Python standard library, so it remains usable on the older DS216play.
"""

import argparse
import hashlib
import json
import os
import ssl
import struct
import sys
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# meteo.kolka.lv serves an incomplete HTTPS certificate chain on older DSM.
# Its public, read-only image is also available over HTTP.  Windguru uploads
# remain on verified HTTPS below.
IMAGE_URL = "http://meteo.kolka.lv/wind1.png"
TEMPERATURE_IMAGE_URL = "http://meteo.kolka.lv/lcd_pagasts.png"
UPLOAD_URL = "https://windguru.cz/upload/api.php"
MS_TO_KNOTS = 1.94384
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kolka_config.json")


def tls_context():
    """Use certifi when available because older DSM CA stores are outdated."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())

# Glyphs used by the fixed bitmap font in wind.png.  Eight is included from
# the same font design even when it is not present in a particular snapshot.
GLYPHS = {
    "0": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "1": (".#.", "##.", ".#.", ".#.", ".#.", ".#.", ".#.", "###"),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    "3": (".###.", "#...#", "....#", "..##.", "....#", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#.", "...#."),
    "5": ("#####", "#....", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "...#.", "..#..", "..#..", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
}

# The large temperature display uses a second, 12-row dotted font.  Templates
# are compared after trimming, so the different natural widths of the digits
# are preserved.  Recognition is confidence-checked before a value is used.
TEMP_GLYPHS = {
    "0": ("..#####..", ".#.....#.", "#.......#", "#.......#", "#.......#", "#.......#", "#.......#", "#.......#", "#.......#", "#.......#", ".#.....#.", "..#####.."),
    "1": ("..#..", "###..", "..#..", "..#..", "..#..", "..#..", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "2": (".#####.", "#.....#", "#.....#", "......#", "......#", ".....#.", "....#..", "...#...", "..#....", ".#.....", "#......", "#######"),
    "3": (".###..", "#...#.", ".....#", ".....#", "....#.", ".###..", "....#.", ".....#", ".....#", ".....#", "#...#.", ".###.."),
    "4": (".....#...", "....##...", "...#.#...", "..#..#...", ".#...#...", "#....#...", "#########", ".....#...", ".....#...", ".....#...", ".....#...", "....###.."),
    "5": (".#######.", ".#.......", ".#.......", ".#.......", ".#.......", ".######..", ".......#.", "........#", "........#", "#.......#", ".#.....#.", "..#####.."),
    "6": (".....##..", "....#....", "...#.....", "..#......", ".#.......", ".#.......", "#..####..", "#.#....#.", "##......#", "##......#", ".##....#.", "..#####.."),
    "7": ("#########", "#......#.", "......#..", ".....#...", ".....#...", "....#....", "...#.....", "...#.....", "..#......", "..#......", ".#.......", "###......"),
    "8": ("..#####..", ".#.....#.", "#.......#", "#.......#", ".#.....#.", "..#####..", ".#.....#.", "#.......#", "#.......#", "#.......#", ".#.....#.", "..#####.."),
    "9": ("..#####..", ".#....##.", "#......##", "#......##", ".#....#.#", "..####..#", "........#", ".......#.", "......#..", ".....#...", "....#....", "..##....."),
}


class ImageFormatError(ValueError):
    pass


class OcrError(ValueError):
    pass


def paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png(data):
    """Decode a non-interlaced 8-bit RGB/RGBA PNG using the standard library."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ImageFormatError("Downloaded file is not a PNG")

    pos = 8
    width = height = bit_depth = color_type = interlace = None
    compressed = []
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
        elif kind == b"IDAT":
            compressed.append(payload)
        elif kind == b"IEND":
            break

    if None in (width, height, bit_depth, color_type, interlace):
        raise ImageFormatError("PNG has no valid IHDR")
    if bit_depth != 8 or color_type not in (2, 6) or interlace != 0:
        raise ImageFormatError(
            "Unsupported PNG format (need non-interlaced 8-bit RGB or RGBA)"
        )

    channels = 3 if color_type == 2 else 4
    stride = width * channels
    raw = zlib.decompress(b"".join(compressed))
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise ImageFormatError("Unexpected decompressed PNG size")

    rows = []
    offset = 0
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw[offset]
        source = raw[offset + 1:offset + 1 + stride]
        offset += stride + 1
        row = bytearray(stride)
        for i, value in enumerate(source):
            left = row[i - channels] if i >= channels else 0
            up = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filter_type == 0:
                decoded = value
            elif filter_type == 1:
                decoded = value + left
            elif filter_type == 2:
                decoded = value + up
            elif filter_type == 3:
                decoded = value + ((left + up) // 2)
            elif filter_type == 4:
                decoded = value + paeth(left, up, upper_left)
            else:
                raise ImageFormatError("Unsupported PNG filter {}".format(filter_type))
            row[i] = decoded & 0xFF
        rows.append(row)
        previous = row

    return width, height, channels, rows


def black_mask(width, height, channels, rows, threshold=220):
    mask = []
    for y in range(height):
        row = rows[y]
        mask.append([
            max(row[x * channels:x * channels + 3]) < threshold
            for x in range(width)
        ])
    return mask


def consecutive_groups(values):
    groups = []
    for value in values:
        if not groups or value != groups[-1][-1] + 1:
            groups.append([value])
        else:
            groups[-1].append(value)
    return groups


def find_table(mask, width, height):
    search_y = int(height * 0.78)
    long_rows = [
        y for y in range(search_y, height)
        if sum(mask[y]) >= int(width * 0.80)
    ]
    row_lines = consecutive_groups(long_rows)
    if len(row_lines) < 3:
        raise OcrError("Could not locate the wind table row lines")
    row_lines = row_lines[-3:]

    table_top = row_lines[0][0]
    column_lines = []
    needed = int((height - table_top) * 0.65)
    for x in range(width):
        if sum(mask[y][x] for y in range(table_top, height)) >= needed:
            column_lines.append(x)
    columns = consecutive_groups(column_lines)
    dividers = [group[len(group) // 2] for group in columns]
    if len(dividers) != 3:
        raise OcrError("Could not locate the three wind table column dividers")

    rows = [
        (row_lines[0][-1] + 1, row_lines[1][0]),
        (row_lines[1][-1] + 1, row_lines[2][0]),
        (row_lines[2][-1] + 1, height),
    ]
    return dividers, rows


def bitmap_distance(candidate, template):
    """Compare a cropped glyph with a template, resizing only if necessary."""
    ch = len(candidate)
    cw = len(candidate[0])
    th = len(template)
    tw = len(template[0])
    mismatches = 0
    for ty in range(th):
        cy = min(ch - 1, int((ty + 0.5) * ch / th))
        for tx in range(tw):
            cx = min(cw - 1, int((tx + 0.5) * cw / tw))
            expected = template[ty][tx] == "#"
            if candidate[cy][cx] != expected:
                mismatches += 1
    size_penalty = abs(cw - tw) * 2 + abs(ch - th) * 2
    return mismatches + size_penalty


def recognize_number(mask, box):
    x0, y0, x1, y1 = box
    active_columns = [
        x for x in range(x0, x1)
        if any(mask[y][x] for y in range(y0, y1))
    ]
    groups = consecutive_groups(active_columns)
    if not groups:
        raise OcrError("Numeric cell is empty")

    result = []
    diagnostics = []
    for group in groups:
        gx0, gx1 = group[0], group[-1] + 1
        active_rows = [
            y for y in range(y0, y1)
            if any(mask[y][x] for x in range(gx0, gx1))
        ]
        if not active_rows:
            continue
        gy0, gy1 = active_rows[0], active_rows[-1] + 1
        glyph = [
            [mask[y][x] for x in range(gx0, gx1)]
            for y in range(gy0, gy1)
        ]
        gw, gh = gx1 - gx0, gy1 - gy0
        if gw <= 2 and gh <= 3:
            char = "."
            score = 0
        elif gh <= 2 and gw >= 2:
            char = "-"
            score = 0
        else:
            scores = {
                char: bitmap_distance(glyph, template)
                for char, template in GLYPHS.items()
            }
            char, score = min(scores.items(), key=lambda item: item[1])
            if score > 10:
                raise OcrError(
                    "Unrecognized glyph {}x{} (best was {} with score {})".format(
                        gw, gh, char, score
                    )
                )
        result.append(char)
        diagnostics.append((char, score))

    text = "".join(result)
    try:
        value = float(text)
    except ValueError:
        raise OcrError("OCR result is not a number: {!r}".format(text))
    return value, text, diagnostics


def extract_wind(png_data):
    width, height, channels, rows = decode_png(png_data)
    if width < 300 or height < 400:
        raise ImageFormatError("wind.png has unexpected dimensions {}x{}".format(width, height))
    mask = black_mask(width, height, channels, rows)
    dividers, table_rows = find_table(mask, width, height)

    average_value = (dividers[0] + 1, table_rows[1][0], dividers[1], table_rows[1][1])
    gust_value = (dividers[0] + 1, table_rows[2][0], dividers[1], table_rows[2][1])
    direction_value = (dividers[2] + 1, table_rows[0][0], width, table_rows[0][1])

    average, average_text, _ = recognize_number(mask, average_value)
    recent_gust, gust_text, _ = recognize_number(mask, gust_value)
    direction, direction_text, _ = recognize_number(mask, direction_value)

    if not (0 <= average <= 80 and 0 <= recent_gust <= 100):
        raise OcrError("Wind speed is outside the safety range")
    if not (0 <= direction <= 360):
        raise OcrError("Wind direction is outside 0..360 degrees")

    return {
        "average_ms": average,
        "recent_gust_ms": recent_gust,
        "direction_degrees": direction,
        "ocr": {
            "average": average_text,
            "recent_gust": gust_text,
            "direction": direction_text,
        },
    }


def temperature_dot_columns(width, height, channels, rows):
    """Convert the blue 3x3 dots into positioned 12-row bitmap columns."""
    blue_points = []
    for y in range(height):
        row = rows[y]
        for x in range(width):
            red, green, blue = row[x * channels:x * channels + 3]
            if blue > 100 and blue > red * 1.5 and blue > green * 1.5:
                blue_points.append((x, y))
    if not blue_points:
        raise OcrError("Temperature image contains no blue display dots")

    x_values = sorted({x for x, _ in blue_points})
    y_values = sorted({y for _, y in blue_points})
    x_runs = consecutive_groups(x_values)
    y_runs = consecutive_groups(y_values)
    y_offset = y_runs[0][0] % 4
    # The proportional font starts individual glyphs at different x phases,
    # so only the vertical dot grid is global.
    if any(len(group) > 3 for group in x_runs):
        raise OcrError("Temperature dot grid has changed horizontally")
    if any(len(group) > 3 or group[0] % 4 != y_offset for group in y_runs):
        raise OcrError("Temperature dot grid has changed vertically")

    blue_set = set(blue_points)
    grid_height = ((height - 1 - y_offset) // 4) + 1
    columns = []
    for x_group in x_runs:
        bits = []
        for gy in range(grid_height):
            y = y_offset + gy * 4
            bits.append(any(
                (xx, yy) in blue_set
                for yy in range(y, min(y + 3, height))
                for xx in x_group
            ))
        columns.append((x_group[0], bits))
    return columns


def crop_temperature_glyph(columns, x0, x1):
    selected = [bits for x, bits in columns if x0 <= x < x1]
    if not selected:
        return None
    height = len(selected[0])
    active_y = [y for y in range(height) if any(bits[y] for bits in selected)]
    gy0, gy1 = active_y[0], active_y[-1] + 1
    return [[bits[y] for bits in selected] for y in range(gy0, gy1)]


def recognize_temperature_glyph(glyph):
    height = len(glyph)
    width = len(glyph[0])
    if height <= 2 and width >= 2:
        return "-", 0

    scores = sorted(
        (bitmap_distance(glyph, template), char)
        for char, template in TEMP_GLYPHS.items()
    )
    best_score, best_char = scores[0]
    next_score = scores[1][0]
    # Exact observed glyphs score zero.  The wider allowance supports the
    # remaining digits while the margin prevents ambiguous uploads.
    if best_score > 26 or (best_score > 0 and next_score - best_score < 3):
        raise OcrError(
            "Uncertain temperature glyph {}x{} ({}:{}, next:{})".format(
                width, height, best_char, best_score, next_score
            )
        )
    return best_char, best_score


def extract_temperature(png_data):
    width, height, channels, rows = decode_png(png_data)
    if width < 300 or not (40 <= height <= 60):
        raise ImageFormatError(
            "lcd_pagasts.png has unexpected dimensions {}x{}".format(width, height)
        )
    columns = temperature_dot_columns(width, height, channels, rows)

    decimal_candidates = []
    for index in range(len(columns) - 1):
        x, left = columns[index]
        next_x, right = columns[index + 1]
        if not (80 <= x <= 260) or next_x - x != 4:
            continue
        left_rows = {y for y, value in enumerate(left) if value}
        right_rows = {y for y, value in enumerate(right) if value}
        if left_rows == {10, 11} and right_rows == {10, 11}:
            decimal_candidates.append(x)
    if len(decimal_candidates) != 1:
        raise OcrError(
            "Could not uniquely locate temperature decimal point: {}".format(
                decimal_candidates
            )
        )
    decimal_x = decimal_candidates[0]

    # The value is right-aligned in fixed character slots around the decimal.
    slots = [
        (decimal_x - 124, decimal_x - 88),
        (decimal_x - 88, decimal_x - 52),
        (decimal_x - 52, decimal_x),
        (decimal_x + 16, decimal_x + 62),
        (decimal_x + 62, decimal_x + 110),
    ]
    recognized = []
    diagnostics = []
    for index, (x0, x1) in enumerate(slots):
        glyph = crop_temperature_glyph(columns, x0, x1)
        if glyph is None:
            if index < 3:
                continue
            raise OcrError("Temperature fractional digit slot is empty")
        char, score = recognize_temperature_glyph(glyph)
        recognized.append((index, char))
        diagnostics.append({"slot": index, "character": char, "score": score})

    whole = "".join(char for index, char in recognized if index < 3)
    fraction = "".join(char for index, char in recognized if index >= 3)
    if not whole or len(fraction) != 2 or "-" in fraction:
        raise OcrError("Invalid temperature character layout")
    if "-" in whole[1:] or whole.count("-") > 1:
        raise OcrError("Invalid temperature minus-sign position")
    text = whole + "." + fraction
    try:
        value = float(text)
    except ValueError:
        raise OcrError("Temperature OCR result is not a number: {!r}".format(text))
    if not (-50 <= value <= 60):
        raise OcrError("Temperature is outside the safety range: {}".format(value))
    return value, text, diagnostics


def fetch_image(url, label, max_age_minutes):
    request = Request(
        url,
        headers={"User-Agent": "Kolka-Windguru-uploader/1.0"},
    )
    response = urlopen(request, timeout=20, context=tls_context())
    content_type = response.headers.get("Content-Type", "")
    if "image/png" not in content_type.lower():
        raise RuntimeError("Expected image/png, received {!r}".format(content_type))

    last_modified = response.headers.get("Last-Modified")
    if last_modified and max_age_minutes > 0:
        modified = parsedate_to_datetime(last_modified)
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - modified).total_seconds()
        if age_seconds > max_age_minutes * 60:
            raise RuntimeError(
                "{} is stale: Last-Modified is {} minutes old".format(
                    label,
                    int(age_seconds / 60)
                )
            )
    return response.read(), last_modified


def windguru_params(reading, uid, password):
    salt = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    digest = hashlib.md5((salt + uid + password).encode("utf-8")).hexdigest()
    minimum_knots = reading["average_ms"] * MS_TO_KNOTS
    gust_knots = reading["recent_gust_ms"] * MS_TO_KNOTS
    average_knots = (minimum_knots + gust_knots) / 2
    params = {
        "uid": uid,
        "salt": salt,
        "hash": digest,
        # Kolka/Windguru mapping requested by the station owner:
        # Videji -> minimum, Nesenas brazmas -> maximum/gust.
        # Windguru average is the midpoint of those two observations.
        "wind_avg": "{:.3f}".format(average_knots),
        "wind_max": "{:.3f}".format(gust_knots),
        "wind_min": "{:.3f}".format(minimum_knots),
        "wind_direction": "{:.1f}".format(reading["direction_degrees"]),
    }
    if reading.get("temperature_c") is not None:
        params["temperature"] = "{:.2f}".format(reading["temperature_c"])
    return params


def upload(reading, uid, password):
    params = windguru_params(reading, uid, password)
    request = Request(
        UPLOAD_URL + "?" + urlencode(params),
        headers={"User-Agent": "Kolka-Windguru-uploader/1.0"},
    )
    response = urlopen(request, timeout=20, context=tls_context())
    body = response.read().decode("utf-8", errors="replace")
    print("Windguru response: {}".format(body.strip()))


def load_credentials():
    uid = os.environ.get("KOLKA_WG_UID", "")
    password = os.environ.get("KOLKA_WG_PASSWORD", "")
    if uid and password:
        return uid, password

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError:
        return "", ""
    return str(config.get("uid", "")), str(config.get("password", ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="Read a saved wind.png instead of downloading")
    parser.add_argument(
        "--temperature-image",
        help="Read a saved lcd_pagasts.png instead of downloading",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload to Windguru (otherwise this is a safe dry-run)",
    )
    parser.add_argument(
        "--max-age-minutes",
        type=int,
        default=20,
        help="Reject an older remote image; use 0 to disable (default: 20)",
    )
    args = parser.parse_args()

    if args.image:
        with open(args.image, "rb") as handle:
            png_data = handle.read()
        last_modified = None
    else:
        png_data, last_modified = fetch_image(
            IMAGE_URL, "wind.png", args.max_age_minutes
        )

    reading = extract_wind(png_data)
    temperature_last_modified = None
    temperature_error = None
    try:
        if args.temperature_image:
            with open(args.temperature_image, "rb") as handle:
                temperature_png = handle.read()
        else:
            temperature_png, temperature_last_modified = fetch_image(
                TEMPERATURE_IMAGE_URL,
                "lcd_pagasts.png",
                args.max_age_minutes,
            )
        temperature, temperature_text, temperature_diagnostics = extract_temperature(
            temperature_png
        )
        reading["temperature_c"] = temperature
        reading["temperature_ocr"] = temperature_text
        reading["temperature_ocr_diagnostics"] = temperature_diagnostics
    except Exception as error:
        temperature_error = str(error)
        print(
            "WARNING: temperature omitted: {}".format(temperature_error),
            file=sys.stderr,
        )

    public_result = dict(reading)
    public_result["source_last_modified"] = last_modified
    public_result["temperature_source_last_modified"] = temperature_last_modified
    if temperature_error:
        public_result["temperature_error"] = temperature_error
    public_result["wind_avg_knots"] = round(
        ((reading["average_ms"] + reading["recent_gust_ms"]) / 2) * MS_TO_KNOTS,
        3,
    )
    public_result["wind_min_knots"] = round(reading["average_ms"] * MS_TO_KNOTS, 3)
    public_result["wind_max_knots"] = round(reading["recent_gust_ms"] * MS_TO_KNOTS, 3)
    print(json.dumps(public_result, ensure_ascii=False, indent=2, sort_keys=True))

    if not args.upload:
        print("Dry-run only; add --upload after configuring credentials.")
        return 0

    uid, password = load_credentials()
    if not uid or not password:
        raise RuntimeError(
            "Set KOLKA_WG_UID/KOLKA_WG_PASSWORD or create kolka_config.json "
            "before using --upload"
        )
    upload(reading, uid, password)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        sys.exit(1)
