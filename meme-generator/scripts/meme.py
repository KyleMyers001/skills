#!/usr/bin/env python3
"""
meme.py — Generate classic template memes using the memegen.link API.

Usage examples:
  python3 meme.py drake "one does not simply" "make a meme by hand"
  python3 meme.py list
  python3 meme.py search drake
  python3 meme.py fine "is this fine?" "100% yes & no" --url-only
  python3 meme.py refresh
"""

import argparse
import difflib
import http.client
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
import zlib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# ── Constants ────────────────────────────────────────────────────────────────
API_BASE = "https://api.memegen.link"
CACHE_DIR = Path.home() / ".cache" / "meme-generator"
CACHE_FILE = CACHE_DIR / "templates.json"
CACHE_TTL_DAYS = 7
USER_AGENT = "meme-generator/1.0"
TIMEOUT = 20  # Per-socket timeout, also bounded by the supervised worker.
TRANSFER_TIMEOUT = 30
MAX_REDIRECTS = 5
MAX_TEMPLATE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_DIMENSION = 4096
MAX_PIXELS = 16_000_000
MAX_TOTAL_PIXELS = 32_000_000
MAX_FRAMES = 100
MAX_LINES = 32
KNOWN_SUBCOMMANDS = {"list", "search", "refresh", "help"}
IO_ERRORS = (OSError, ValueError, http.client.HTTPException, RecursionError)


class MemeError(ValueError):
    """A safe-to-display error message, without remote content or captions."""


# ── Text encoding ─────────────────────────────────────────────────────────────
def encode_segment(text: str) -> str:
    """Encode a text segment using memegen.link's escaping scheme."""
    if text == "":
        return "_"
    # Apply in order per spec
    text = text.replace("_", "__")
    text = text.replace("-", "--")
    text = text.replace(" ", "_")
    text = text.replace("\n", "~n")
    text = text.replace("?", "~q")
    text = text.replace("&", "~a")
    text = text.replace("%", "~p")
    text = text.replace("#", "~h")
    text = text.replace("/", "~s")
    text = text.replace("\\", "~b")
    text = text.replace("<", "~l")
    text = text.replace(">", "~g")
    text = text.replace('"', "''")
    # Percent-encode remaining unsafe chars (keep our escape chars safe)
    text = urllib.parse.quote(text, safe="~_-'")
    return text


# ── Network helpers ───────────────────────────────────────────────────────────
def read_bounded(stream, limit: int) -> bytes:
    """Enforce the actual byte count, even without an honest Content-Length."""
    data = bytearray()
    while len(data) <= limit:
        chunk = stream.read(min(65536, limit + 1 - len(data)))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
    raise MemeError("Response exceeds the size limit.")


def validate_fetch_url(url: str) -> None:
    """Only the approved HTTPS origin may receive initial or redirected GETs."""
    try:
        if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
            raise ValueError
        parsed = urllib.parse.urlsplit(url)
        allowed = (parsed.scheme == "https"
                   and parsed.hostname == "api.memegen.link"
                   and parsed.port in (None, 443)
                   and parsed.username is None and parsed.password is None)
    except ValueError:
        allowed = False
    if not allowed:
        raise MemeError("Network destination is not an approved HTTPS origin.")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            destination = urllib.parse.urljoin(req.full_url, newurl)
            validate_fetch_url(destination)
            if req.get_method() != "GET":
                raise MemeError("Only GET requests may be redirected.")
        except ValueError:
            fp.close()
            raise
        return urllib.request.Request(destination, headers=req.headers,
                                      origin_req_host=req.origin_req_host,
                                      unverifiable=True)

    # Python 3.9 does not supply the 308 alias on every patch release.
    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_302


def fetch_url_in_process(url: str, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Worker-side bounded fetch; the parent also limits total elapsed time."""
    validate_fetch_url(url)
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_IMAGE_BYTES:
        raise MemeError("Invalid download byte limit.")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(SafeRedirectHandler())
    try:
        response = opener.open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:
        error.close()
        raise
    with response as resp:
        length = resp.headers.get("Content-Length")
        if length is not None and not 0 <= int(length) <= max_bytes:
            raise MemeError("Response exceeds the size limit.")
        return read_bounded(resp, max_bytes)


def fetch_url(url: str, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Supervise even blocked DNS/TLS/header reads and slowly progressing bodies."""
    validate_fetch_url(url)
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_IMAGE_BYTES:
        raise MemeError("Invalid download byte limit.")
    command = [sys.executable, str(Path(__file__).absolute()), "--fetch-worker"]
    payload = json.dumps({"url": url, "max_bytes": max_bytes}).encode("utf-8")
    try:
        result = subprocess.run(command, input=payload, capture_output=True,
                                timeout=TRANSFER_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        # run() kills and reaps the child; its exception can include private
        # partial output, so never interpolate it into a user-facing message.
        raise MemeError("Network fetch exceeded the transfer deadline.") from None
    except OSError:
        raise MemeError("Could not start the network worker.") from None
    if result.returncode == 2 and re.fullmatch(rb"[1-5][0-9]{2}", result.stderr):
        # Give both Python 3.9 and newer versions an initialized, closed body.
        error = urllib.error.HTTPError(url, int(result.stderr),
                                       "HTTP request failed", None, io.BytesIO())
        error.close()
        raise error
    if result.returncode != 0:
        raise MemeError("Network fetch failed or exceeded a safety limit.")
    if len(result.stdout) > max_bytes:
        raise MemeError("Response exceeds the size limit.")
    return result.stdout


def fetch_worker() -> None:
    """Private stdin/binary-stdout protocol; captions never enter worker argv."""
    request = json.loads(read_bounded(sys.stdin.buffer, MAX_IMAGE_BYTES))
    if not isinstance(request, dict) or set(request) != {"url", "max_bytes"}:
        raise MemeError("Invalid network worker request.")
    try:
        data = fetch_url_in_process(request["url"], request["max_bytes"])
    except urllib.error.HTTPError as error:
        # Python 3.9 skips response initialization when an error has no body.
        if "file" in vars(error):
            error.close()
        sys.stderr.write(str(error.code))
        raise SystemExit(2) from None
    sys.stdout.buffer.write(data)


def fetch_json(url: str) -> object:
    """Fetch bounded template JSON."""
    return json.loads(fetch_url(url, max_bytes=MAX_TEMPLATE_BYTES))


# ── Template cache ────────────────────────────────────────────────────────────
def validate_templates(value: object) -> list:
    """Validate fields used as paths, terminal text, or loop bounds."""
    if not isinstance(value, list):
        raise MemeError("Invalid template catalog.")
    for template in value:
        if not isinstance(template, dict):
            raise MemeError("Invalid template record.")
        tid, name = template.get("id"), template.get("name")
        lines = template.get("lines")
        if (not isinstance(tid, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", tid) is None):
            raise MemeError("Invalid template ID.")
        if (not isinstance(name, str) or not name.strip()
                or len(name) > 512 or not name.isprintable()):
            raise MemeError("Invalid template name.")
        if type(lines) is not int or not 0 <= lines <= MAX_LINES:
            raise MemeError("Invalid template caption count.")

    # The upstream catalog can publish multiple records for the same route.
    # Validate all records before selecting metadata or printing a warning.
    normalized, conflicting = {}, False
    for template in value:
        previous = normalized.get(template["id"])
        if previous is not None:
            conflicting |= ((previous["name"], previous["lines"])
                            != (template["name"], template["lines"]))
            if template["lines"] <= previous["lines"]:
                continue
        normalized[template["id"]] = template
    if conflicting:
        print("Warning: duplicate template metadata differs; "
              "using the largest validated caption count.", file=sys.stderr)
    return list(normalized.values())


def cache_age_days() -> Optional[float]:
    try:
        return (time.time() - CACHE_FILE.stat().st_mtime) / 86400
    except OSError:
        return None


def load_cache() -> Optional[list]:
    """Return a bounded, validated cache, or None if unusable."""
    try:
        with open(CACHE_FILE, "rb") as stream:
            return validate_templates(json.loads(
                read_bounded(stream, MAX_TEMPLATE_BYTES)))
    except IO_ERRORS:
        return None


def save_cache(templates: list) -> None:
    """Replace the cache only after validation and a complete temporary write."""
    templates = validate_templates(templates)
    data = json.dumps(templates, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_TEMPLATE_BYTES:
        raise MemeError("Template cache exceeds the size limit.")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=CACHE_DIR, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, CACHE_FILE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def fetch_templates_from_api() -> list:
    return validate_templates(fetch_json(f"{API_BASE}/templates/"))


def get_templates(force_refresh: bool = False) -> list:
    """Use fresh validated data, or a valid stale cache if refresh fails."""
    cached = load_cache()
    age = cache_age_days()
    if cached is not None and not force_refresh and age is not None:
        if age <= CACHE_TTL_DAYS:
            return cached
    try:
        templates = validate_templates(fetch_templates_from_api())
        save_cache(templates)
        return templates
    except IO_ERRORS:
        if cached is not None:
            print("Warning: template refresh failed; using stale cache.",
                  file=sys.stderr)
            return cached
        raise MemeError("Could not refresh templates; no valid cache is available.")


def build_template_index(templates: list) -> dict:
    """Build a dict keyed by template id for quick lookup."""
    return {t["id"]: t for t in validate_templates(templates)}


# ── URL building ──────────────────────────────────────────────────────────────
def build_image_url(
    template_id: str,
    lines: list[str],
    ext: str = "png",
    width: Optional[int] = None,
    height: Optional[int] = None,
    font: Optional[str] = None,
) -> str:
    """Construct the memegen.link image URL for the given template and text."""
    encoded = [encode_segment(line) for line in lines]
    path_segments = "/".join(encoded)
    url = f"{API_BASE}/images/{template_id}/{path_segments}.{ext}"

    params = {}
    if width is not None:
        params["width"] = str(width)
    if height is not None:
        params["height"] = str(height)
    if font is not None:
        params["font"] = font

    if params:
        url += "?" + urllib.parse.urlencode(params)

    return url


# ── Image validation ──────────────────────────────────────────────────────────
def within_image_budget(width: int, height: int, frames: int = 1) -> bool:
    """Count full composited canvases, even when animation patches are tiny."""
    return (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION
            and 0 < frames <= MAX_FRAMES and width * height <= MAX_PIXELS
            and width * height * frames <= MAX_TOTAL_PIXELS)


def frame_fits(width, height, x, y, frame_width, frame_height) -> bool:
    return (within_image_budget(frame_width, frame_height) and x >= 0 and y >= 0
            and x + frame_width <= width and y + frame_height <= height)


def validate_png(data: bytes) -> bool:
    if len(data) < 57 or data[:16] != b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR":
        return False
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if not within_image_budget(width, height):
        return False
    offset, has_pixels, has_idat, idat_ended = 8, False, False, False
    declared, controls, sequence = None, 0, 0
    default_frame, frame_uses_idat, frame_has_data = False, False, False
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data) or size > 0x7fffffff:
            return False
        payload = data[offset + 8:end - 4]
        checksum = int.from_bytes(data[end - 4:end], "big")
        if zlib.crc32(data[offset + 4:end - 4]) != checksum:
            return False
        if kind == b"IHDR":
            depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8),
                      4: (8, 16), 6: (8, 16)}
            if (offset != 8 or size != 13 or payload[8] not in depths.get(payload[9], ())
                    or payload[10:12] != b"\0\0" or payload[12] not in (0, 1)):
                return False
        elif kind == b"acTL":
            if declared is not None or has_idat or size != 8:
                return False
            declared = int.from_bytes(payload[:4], "big")
            if not within_image_budget(width, height, declared):
                return False
        elif kind == b"fcTL":
            if declared is None or size != 26 or (controls and not frame_has_data):
                return False
            if int.from_bytes(payload[:4], "big") != sequence:
                return False
            sequence += 1
            fw, fh, x, y = (int.from_bytes(payload[i:i + 4], "big")
                            for i in (4, 8, 12, 16))
            if (not frame_fits(width, height, x, y, fw, fh)
                    or payload[24] > 2 or payload[25] > 1):
                return False
            frame_uses_idat = not has_idat
            if frame_uses_idat and (controls or (x, y, fw, fh) != (0, 0, width, height)):
                return False
            default_frame |= frame_uses_idat
            controls += 1
            if controls > declared:
                return False
            frame_has_data = False
        elif kind == b"IDAT":
            if idat_ended or (controls and not frame_uses_idat):
                return False
            has_idat = True
            has_pixels |= size > 0
            if frame_uses_idat:
                frame_has_data |= size > 0
        elif kind == b"fdAT":
            if not has_pixels or not controls or frame_uses_idat or size < 4:
                return False
            if int.from_bytes(payload[:4], "big") != sequence:
                return False
            sequence += 1
            frame_has_data |= size > 4
        elif kind == b"IEND":
            if size or not has_pixels or end != len(data):
                return False
            if declared is None:
                return True
            # A default image outside the animation is another decoded canvas.
            return (controls == declared and frame_has_data
                    and within_image_budget(width, height,
                                            controls + (not default_frame)))
        elif kind != b"PLTE" and not kind[0] & 32:
            return False  # Unknown critical chunks may change image semantics.
        if has_idat and kind != b"IDAT":
            idat_ended = True
        offset = end
    return False


def validate_jpeg(data: bytes) -> bool:
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        return False
    offset, has_frame, has_scan, entropy, has_pixels = 2, False, False, False, False
    while offset < len(data):
        if entropy:
            next_marker = data.find(b"\xff", offset)
            if next_marker < 0:
                return False
            has_pixels |= next_marker > offset
            offset = next_marker
        if offset + 2 > len(data) or data[offset] != 255:
            return False
        marker = data[offset + 1]
        if marker == 255:
            offset += 1
            continue
        if entropy and (marker == 0 or 0xD0 <= marker <= 0xD7):
            has_pixels = True
            offset += 2
            continue
        entropy = False
        if marker == 0xD9:
            return has_frame and has_scan and has_pixels and offset + 2 == len(data)
        if marker in (0, 0xD8, 0xDC) or offset + 4 > len(data):
            return False
        size = int.from_bytes(data[offset + 2:offset + 4], "big")
        end = offset + 2 + size
        if size < 2 or end > len(data) - 2:
            return False
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if marker not in (0xC0, 0xC1, 0xC2) or has_frame or size < 11:
                return False
            height = int.from_bytes(data[offset + 5:offset + 7], "big")
            width = int.from_bytes(data[offset + 7:offset + 9], "big")
            components = data[offset + 9]
            if (not within_image_budget(width, height) or not 1 <= components <= 4
                    or size != 8 + 3 * components):
                return False
            has_frame = True
        if marker == 0xDA:
            if not has_frame or size < 8:
                return False
            components = data[offset + 4]
            if not 1 <= components <= 4 or size != 6 + 2 * components:
                return False
            has_scan, entropy = True, True
        offset = end
    return False


def gif_subblocks(data: bytes, offset: int):
    has_data = False
    while offset < len(data):
        size = data[offset]
        offset += 1
        if size == 0:
            return offset, has_data
        if offset + size > len(data):
            break
        has_data = True
        offset += size
    raise ValueError("Truncated GIF subblocks.")


def validate_gif(data: bytes) -> bool:
    if len(data) < 29 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return False
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    if not within_image_budget(width, height):
        return False
    offset, frames = 13, 0
    if data[10] & 0x80:
        offset += 3 * (2 ** ((data[10] & 7) + 1))
    while offset < len(data):
        marker = data[offset]
        offset += 1
        if marker == 0x3B:
            return offset == len(data) and within_image_budget(width, height, frames)
        if marker == 0x21:
            if offset >= len(data) or data[offset] not in (0xF9, 0xFE, 0xFF):
                return False  # Plain-text/unknown rendering extensions are unsupported.
            label = data[offset]
            offset += 1
            if label == 0xF9 and (data[offset] != 4 or data[offset + 5] != 0):
                return False
            if label == 0xFF and data[offset] != 11:
                return False
            offset, _ = gif_subblocks(data, offset)
            continue
        if marker != 0x2C or offset + 9 > len(data):
            return False
        x, y, fw, fh = (int.from_bytes(data[offset + i:offset + i + 2], "little")
                        for i in (0, 2, 4, 6))
        if not frame_fits(width, height, x, y, fw, fh):
            return False
        packed = data[offset + 8]
        offset += 9
        if packed & 0x80:
            offset += 3 * (2 ** ((packed & 7) + 1))
        if offset >= len(data) or not 2 <= data[offset] <= 8:
            return False
        offset, has_data = gif_subblocks(data, offset + 1)
        frames += 1
        if not has_data or not within_image_budget(width, height, frames):
            return False
    return False


def webp_chunks(data: bytes, offset: int = 12):
    while offset + 8 <= len(data):
        kind = data[offset:offset + 4]
        size = int.from_bytes(data[offset + 4:offset + 8], "little")
        end = offset + 8 + size
        if end + size % 2 > len(data) or (size % 2 and data[end] != 0):
            raise ValueError("Truncated or invalid WebP chunk.")
        yield kind, data[offset + 8:end]
        offset = end + size % 2
    if offset != len(data):
        raise ValueError("Truncated WebP chunk header.")


def webp_dimensions(kind: bytes, payload: bytes):
    if kind == b"VP8 ":
        if (len(payload) < 10 or payload[0] & 1 or ((payload[0] >> 1) & 7) > 3
                or payload[3:6] != b"\x9d\x01\x2a"
                or payload[7] & 0xC0 or payload[9] & 0xC0):
            raise ValueError("Unsupported VP8 frame header.")
        width = int.from_bytes(payload[6:8], "little")
        height = int.from_bytes(payload[8:10], "little")
    elif kind == b"VP8L":
        if len(payload) < 5 or payload[0] != 0x2F or payload[4] & 0xE0:
            raise ValueError("Unsupported VP8L frame header.")
        bits = int.from_bytes(payload[1:5], "little")
        width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    else:
        raise ValueError("Unknown WebP bitstream.")
    if not within_image_budget(width, height):
        raise ValueError("WebP dimensions exceed the image budget.")
    return width, height


def validate_webp(data: bytes) -> bool:
    if (len(data) < 26 or data[:4] != b"RIFF" or data[8:12] != b"WEBP"
            or int.from_bytes(data[4:8], "little") + 8 != len(data)):
        return False
    canvas, bitstream = None, None
    animated, has_anim, frames = False, False, 0
    for index, (kind, payload) in enumerate(webp_chunks(data)):
        if kind == b"VP8X":
            if (index != 0 or len(payload) != 10 or payload[0] & 0xC1
                    or payload[1:4] != b"\0\0\0"):
                return False
            canvas = (int.from_bytes(payload[4:7], "little") + 1,
                      int.from_bytes(payload[7:10], "little") + 1)
            if not within_image_budget(*canvas):
                return False
            animated = bool(payload[0] & 2)
        elif kind == b"ANIM":
            if not animated or has_anim or frames or len(payload) != 6:
                return False
            has_anim = True
        elif kind == b"ANMF":
            if not has_anim or len(payload) < 30 or payload[15] & 0xFC:
                return False
            # ANMF offsets are in two-pixel units; dimensions are stored minus one.
            x, y = (2 * int.from_bytes(payload[i:i + 3], "little") for i in (0, 3))
            fw, fh = (int.from_bytes(payload[i:i + 3], "little") + 1 for i in (6, 9))
            if not frame_fits(*canvas, x, y, fw, fh):
                return False
            frame_bitstream, has_alpha = None, False
            for nested_kind, nested_payload in webp_chunks(payload, 16):
                if nested_kind == b"ALPH":
                    if has_alpha or frame_bitstream is not None or not nested_payload:
                        return False
                    has_alpha = True
                elif nested_kind in (b"VP8 ", b"VP8L"):
                    if frame_bitstream is not None or (has_alpha and nested_kind == b"VP8L"):
                        return False
                    frame_bitstream = webp_dimensions(nested_kind, nested_payload)
                elif nested_kind in (b"VP8X", b"ANIM", b"ANMF"):
                    return False
            if frame_bitstream != (fw, fh):
                return False
            frames += 1
            if not within_image_budget(*canvas, frames):
                return False
        elif kind in (b"VP8 ", b"VP8L"):
            if animated or bitstream is not None:
                return False
            bitstream = webp_dimensions(kind, payload)
            if canvas is not None and bitstream != canvas:
                return False
    if animated:
        return has_anim and frames > 0 and within_image_budget(*canvas, frames)
    return bitstream is not None


def validate_image_bytes(data: bytes, ext: str) -> bool:
    """Check declared image budgets and containers, never decompress pixels."""
    validators = {"png": validate_png, "jpg": validate_jpeg,
                  "gif": validate_gif, "webp": validate_webp}
    if ext not in validators or len(data) > MAX_IMAGE_BYTES:
        return False
    try:
        return validators[ext](data)
    except (IndexError, ValueError):
        return False


def validate_render_dimensions(width: Optional[int], height: Optional[int]) -> None:
    dimensions = [value for value in (width, height) if value is not None]
    if (any(not 0 < value <= MAX_DIMENSION for value in dimensions)
            or (width is not None and height is not None and width * height > MAX_PIXELS)):
        raise MemeError("Requested dimensions exceed the image limits.")


# ── Output path ───────────────────────────────────────────────────────────────
def resolve_output_path(template_id: str, ext: str, out: Optional[str]) -> Path:
    """Resolve the output file path."""
    if out:
        # Do not resolve the final component: exclusive creation must reject
        # symlinks, including dangling ones, rather than follow their targets.
        return Path(out).expanduser().absolute()
    out_dir = Path(os.environ.get("MEME_OUT_DIR") or "drafts").expanduser()
    return out_dir.absolute() / f"meme-{template_id}-{uuid.uuid4().hex}.{ext}"


# ── Subcommand: render ────────────────────────────────────────────────────────
def cmd_render(args: argparse.Namespace) -> None:
    validate_render_dimensions(args.width, args.height)
    templates = get_templates()
    index = build_template_index(templates)

    template_id = args.template_id
    if template_id not in index:
        print(f"Error: unknown template '{template_id}'.", file=sys.stderr)
        # Suggest close matches on both id and name
        all_ids = list(index.keys())
        all_names = [t["name"] for t in templates]
        id_matches = difflib.get_close_matches(template_id, all_ids, n=5, cutoff=0.4)
        name_matches = difflib.get_close_matches(template_id, all_names, n=5, cutoff=0.4)
        suggestions = list(dict.fromkeys(id_matches + [t["id"] for t in templates if t["name"] in name_matches]))[:5]
        if suggestions:
            print("Did you mean one of:", file=sys.stderr)
            for s in suggestions:
                print(f"  {s}  ({index[s]['name']})", file=sys.stderr)
        sys.exit(1)

    tmpl = index[template_id]
    official_lines = tmpl["lines"]
    text_args = args.text or []
    if len(text_args) > MAX_LINES:
        raise MemeError("Too many captions; at most 32 are supported.")

    if len(text_args) > official_lines:
        print(
            f"Warning: template '{template_id}' officially takes {official_lines} line(s), "
            f"but {len(text_args)} were provided.",
            file=sys.stderr,
        )

    # Pad to at least official_lines with empty strings if fewer were given
    lines = list(text_args)
    while len(lines) < official_lines:
        lines.append("")

    ext = args.ext or "png"
    url = build_image_url(
        template_id,
        lines,
        ext=ext,
        width=args.width,
        height=args.height,
        font=args.font,
    )

    if args.url_only:
        print(url)
        return

    out_path = resolve_output_path(template_id, ext, args.out)

    try:
        data = fetch_url(url)
    except urllib.error.HTTPError as error:
        raise MemeError(f"Image service returned HTTP {error.code}.") from None
    except MemeError:
        raise  # Preserve only our explicitly caption-safe network diagnostics.
    except IO_ERRORS:
        raise MemeError("Image download failed or exceeded the size limit.") from None
    if not validate_image_bytes(data, ext):
        raise MemeError("Image is malformed, unsupported, or exceeds dimension/frame limits.")

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "xb") as stream:
            stream.write(data)
    except FileExistsError:
        raise MemeError("Output already exists; choose a different destination.") from None
    except OSError:
        raise MemeError("Could not write the output image.") from None

    print(str(out_path))


# ── Subcommand: list ──────────────────────────────────────────────────────────
def cmd_list(args: argparse.Namespace) -> None:
    templates = get_templates()
    if args.json:
        print(json.dumps(templates, indent=2))
        return
    _print_templates(templates)


def _print_templates(templates: list) -> None:
    if not templates:
        print("(no templates)")
        return
    max_id_len = max(len(t["id"]) for t in templates)
    for t in templates:
        tid = t["id"].ljust(max_id_len)
        lines = t.get("lines", "?")
        name = t.get("name", "")
        print(f"{tid}  {lines}L  {name}")


# ── Subcommand: search ────────────────────────────────────────────────────────
def cmd_search(args: argparse.Namespace) -> None:
    templates = get_templates()
    query = args.query.lower()

    # Substring match on id or name
    hits = [
        t for t in templates
        if query in t["id"].lower() or query in t.get("name", "").lower()
    ]

    if hits:
        if args.json:
            print(json.dumps(hits, indent=2))
        else:
            _print_templates(hits)
        return

    # Fuzzy fallback
    all_ids = [t["id"] for t in templates]
    all_names = [t.get("name", "") for t in templates]
    index = build_template_index(templates)

    id_matches = difflib.get_close_matches(args.query, all_ids, n=5, cutoff=0.4)
    name_matches = difflib.get_close_matches(args.query, all_names, n=5, cutoff=0.4)
    fuzzy_ids = list(dict.fromkeys(
        id_matches + [t["id"] for t in templates if t.get("name", "") in name_matches]
    ))[:5]

    if fuzzy_ids:
        print("No exact matches. Did you mean:", file=sys.stderr)
        fuzzy_templates = [index[fid] for fid in fuzzy_ids if fid in index]
        _print_templates(fuzzy_templates)
    else:
        print(f"No templates found matching '{args.query}'.", file=sys.stderr)
        sys.exit(1)


# ── Subcommand: refresh ───────────────────────────────────────────────────────
def cmd_refresh(_args: argparse.Namespace) -> None:
    try:
        templates = validate_templates(fetch_templates_from_api())
        save_cache(templates)
    except IO_ERRORS:
        raise MemeError("Could not refresh the template cache.") from None
    print(f"Cached {len(templates)} templates to {CACHE_FILE}")


# ── Argument parsing ──────────────────────────────────────────────────────────
def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meme.py",
        description="Generate memes using the memegen.link API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="subcommand")

    # list
    p_list = sub.add_parser("list", help="List all available templates.")
    p_list.add_argument("--json", action="store_true", help="Dump raw JSON.")

    # search
    p_search = sub.add_parser("search", help="Search templates by id or name.")
    p_search.add_argument("query", help="Search query.")
    p_search.add_argument("--json", action="store_true", help="Dump raw JSON.")

    # refresh
    sub.add_parser("refresh", help="Force re-download of the template index.")

    return parser


def make_render_parser() -> argparse.ArgumentParser:
    """Parser for the render subcommand (no keyword)."""
    parser = argparse.ArgumentParser(
        prog="meme.py <template_id>",
        description="Render a meme from a template.",
    )
    parser.add_argument("template_id", help="Template ID (e.g. 'drake').")
    parser.add_argument("text", nargs="*", help="Text lines for the meme.")
    parser.add_argument("--width", type=int, default=600, help="Image width (default 600).")
    parser.add_argument("--height", type=int, default=None, help="Image height.")
    parser.add_argument("--font", default=None, help="Font name.")
    parser.add_argument("--ext", default="png", choices=["png", "jpg", "gif", "webp"], help="Image format (default png).")
    parser.add_argument("--out", default=None, help="Output file path.")
    parser.add_argument("--url-only", action="store_true", help="Print URL only; do not download.")
    return parser


# ── Entry point ───────────────────────────────────────────────────────────────
def dispatch() -> None:
    argv = sys.argv[1:]
    if argv == ["--fetch-worker"]:
        fetch_worker()
        return
    if argv == ["help"]:
        argv = ["--help"]

    # Detect if this is a render invocation (no known subcommand keyword)
    if not argv or argv[0] in ("-h", "--help"):
        make_parser().parse_args(argv)
        return

    if argv[0] not in KNOWN_SUBCOMMANDS:
        # Render path
        render_parser = make_render_parser()
        args = render_parser.parse_args(argv)
        cmd_render(args)
        return

    # Standard subcommand path
    parser = make_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "list":
        cmd_list(args)
    elif args.subcommand == "search":
        cmd_search(args)
    elif args.subcommand == "refresh":
        cmd_refresh(args)
    else:
        parser.print_help()


def main() -> None:
    try:
        dispatch()
    except MemeError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
    except IO_ERRORS:
        # Library exceptions can contain the caption URL or a remote body.
        print("Error: operation failed; check input, network, and file access.",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
