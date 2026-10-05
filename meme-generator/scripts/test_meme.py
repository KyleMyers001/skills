"""Offline regression tests. Never use the real cache or rendering service."""
import argparse
import base64
import contextlib
from email.message import Message
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request
import urllib.response
import zlib
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("meme.py")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AA"
    "AAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
WEBP = base64.b64decode(
    "UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA"
)
# A real one-pixel JPEG, generated offline from the PNG above.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAA"
    "AAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAAaADAAQAAAABAAAAAQAAAAD/7QA4UGhvdG9z"
    "aG9wIDMuMAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmACZjs+EJ+/8AAEQgA"
    "AQABAwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIB"
    "AwMCBAMFBQQEAAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYX"
    "GBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeI"
    "iYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn"
    "6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIB"
    "AgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDTh"
    "JfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWG"
    "h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm"
    "5+jp6vLz9PX29/j5+v/bAEMAAgICAgICAwICAwUDAwMFBgUFBQUGCAYGBgYGCAoICAgICAgK"
    "CgoKCgoKCgwMDAwMDA4ODg4ODw8PDw8PDw8PD//bAEMBAgICBAQEBwQEBxALCQsQEBAQEBAQ"
    "EBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEP/dAAQAAf/aAAwD"
    "AQACEQMRAD8A+L6KKK/lM/38P//Z"
)
CATALOG = [{"id": "drake", "name": "Drake", "lines": 2}]
FETCH_URL = "https://api.memegen.link/templates/"
REAL_OPENER_OPEN = urllib.request.OpenerDirector.open
REAL_RUN = subprocess.run


def png_chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload)))


def png_image(width, height, frames=None, default_frame=True, declared=None):
    # Header-only size fixtures reuse a tiny compressed stream; never decode them.
    ihdr = struct.pack(">II", width, height) + PNG[24:29]
    compressed = PNG[41:41 + int.from_bytes(PNG[33:37], "big")]
    parts = [PNG[:8], png_chunk(b"IHDR", ihdr)]
    if frames is None:
        parts.append(png_chunk(b"IDAT", compressed))
    else:
        parts.append(png_chunk(b"acTL", struct.pack(">II", declared or frames, 0)))
        sequence = 0
        if not default_frame:
            parts.append(png_chunk(b"IDAT", compressed))
        for index in range(frames):
            parts.append(png_chunk(b"fcTL", struct.pack(
                ">IIIIIHHBB", sequence, width, height, 0, 0, 1, 100, 0, 0)))
            sequence += 1
            if default_frame and index == 0:
                parts.append(png_chunk(b"IDAT", compressed))
            else:
                parts.append(png_chunk(b"fdAT", struct.pack(">I", sequence) + compressed))
                sequence += 1
    return b"".join(parts) + png_chunk(b"IEND", b"")


def jpeg_image(width, height):
    data = bytearray(JPEG)
    offset = data.index(b"\xff\xc0")
    data[offset + 5:offset + 9] = struct.pack(">HH", height, width)
    return bytes(data)


def gif_image(width, height, frames=1, rectangle=None):
    start = GIF.index(b",", 13)
    x, y, frame_width, frame_height = rectangle or (0, 0, width, height)
    image = (b"," + struct.pack("<HHHHB", x, y, frame_width, frame_height, 0)
             + GIF[start + 10:-1])
    return (GIF[:6] + struct.pack("<HH", width, height) + GIF[10:start]
            + image * frames + b";")


def riff_chunk(kind, payload):
    return kind + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) % 2)


def webp_file(*chunks):
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def webp_bitstream(width, height, lossless=False):
    if lossless:
        return riff_chunk(b"VP8L", b"\x2f" + struct.pack(
            "<I", (width - 1) | ((height - 1) << 14)) + b"\0")
    payload = bytearray(WEBP[20:])
    payload[6:10] = struct.pack("<HH", width, height)
    return riff_chunk(b"VP8 ", bytes(payload))


def webp_extended(width, height, frames=None, rectangle=None, bitstream_size=None):
    header = (bytes([2 if frames is not None else 0]) + b"\0" * 3
              + (width - 1).to_bytes(3, "little")
              + (height - 1).to_bytes(3, "little"))
    parts = [riff_chunk(b"VP8X", header)]
    if frames is None:
        parts.append(webp_bitstream(*(bitstream_size or (width, height))))
    else:
        parts.append(riff_chunk(b"ANIM", b"\0" * 6))
        x, y, fw, fh = rectangle or (0, 0, width, height)
        frame = (b"".join(value.to_bytes(3, "little")
                         for value in (x // 2, y // 2, fw - 1, fh - 1, 10))
                 + b"\0" + webp_bitstream(*(bitstream_size or (fw, fh))))
        parts.extend([riff_chunk(b"ANMF", frame)] * frames)
    return webp_file(*parts)


class MemeTests(unittest.TestCase):
    def setUp(self):
        module = runpy.run_path(str(SCRIPT), run_name="test_subject")
        self.g = module["main"].__globals__
        self.temp = tempfile.TemporaryDirectory(prefix="meme-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.responses = []
        self.g["CACHE_DIR"] = self.root / "cache"
        self.g["CACHE_FILE"] = self.root / "cache" / "templates.json"
        self.enter_context(patch.dict(os.environ, MEME_OUT_DIR=str(self.root / "out")))
        # All unexpected HTTP requests fail rather than reaching the network.
        self.network = self.enter_context(patch(
            "urllib.request.OpenerDirector.open", autospec=True))
        self.network.side_effect = AssertionError("unexpected network request")
        self.enter_context(patch("urllib.request.AbstractHTTPHandler.do_open",
                                 side_effect=AssertionError("real HTTP request")))
        self.process = self.enter_context(patch(
            "subprocess.run", side_effect=AssertionError("unexpected worker")))

    def enter_context(self, manager):
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def call(self, name, *args, **kwargs):
        return self.g[name](*args, **kwargs)

    def cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", [str(SCRIPT), *argv]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                self.call("main")
            except SystemExit as exc:
                return exc.code, stdout.getvalue(), stderr.getvalue()
        return 0, stdout.getvalue(), stderr.getvalue()

    def seed_cache(self, value=CATALOG):
        path = self.g["CACHE_FILE"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def render_setup(self, data=PNG):
        self.seed_cache()
        return self.enter_context(patch.dict(self.g, fetch_url=lambda *a, **k: data))

    def test_default_names_are_unique(self):
        paths = [self.call("resolve_output_path", "drake", "png", None)
                 for _ in range(20)]
        self.assertEqual(len(paths), len(set(paths)))

    def test_normal_render_prints_only_path(self):
        self.render_setup()
        code, out, err = self.cli("drake", "private-caption", "second")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(out.splitlines()), 1)
        self.assertEqual(Path(out.strip()).read_bytes(), PNG)

    def test_explicit_missing_parent_created(self):
        self.render_setup()
        target = self.root / "nested" / "meme.png"
        code, out, err = self.cli("drake", "first", "second", "--out", str(target))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(target.read_bytes(), PNG)

    def test_existing_output_not_overwritten(self):
        self.render_setup()
        target = self.root / "existing.png"
        target.write_bytes(b"keep")
        code, _, _ = self.cli("drake", "--out", str(target))
        self.assertNotEqual(code, 0)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_symlink_output_refused(self):
        self.render_setup()
        for existing in (False, True):
            with self.subTest(existing=existing):
                target = self.root / str(existing)
                if existing:
                    target.write_bytes(b"keep")
                link = self.root / (str(existing) + "-link.png")
                link.symlink_to(target)
                code, _, _ = self.cli("drake", "--out", str(link))
                self.assertNotEqual(code, 0)
                self.assertTrue(link.is_symlink())
                if existing:
                    self.assertEqual(target.read_bytes(), b"keep")
                else:
                    self.assertFalse(target.exists())

    def test_concurrent_renders_to_same_file_have_one_winner(self):
        self.render_setup()
        target = self.root / "shared.png"
        args = argparse.Namespace(template_id="drake", text=[], ext="png",
                                  width=600, height=None, font=None,
                                  url_only=False, out=str(target))
        def render(_):
            try:
                self.call("cmd_render", args)
                return True
            except (SystemExit, OSError, ValueError, self.g.get("MemeError", ValueError)):
                return False
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()), ThreadPoolExecutor(2) as pool:
            outcomes = list(pool.map(render, range(2)))
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(target.read_bytes(), PNG)

    def test_invalid_download_does_not_create_output(self):
        self.render_setup(b"\x89PNG")
        target = self.root / "invalid.png"
        code, _, _ = self.cli("drake", "--out", str(target))
        self.assertNotEqual(code, 0)
        self.assertFalse(target.exists())

    def test_write_error_is_safe_and_nonzero(self):
        self.render_setup()
        real_open = open
        attempts = []
        def fail_output_only(path, mode="r", *args, **kwargs):
            if mode == "xb":
                attempts.append(path)
                raise PermissionError("private-caption")
            return real_open(path, mode, *args, **kwargs)
        with patch("builtins.open", side_effect=fail_output_only):
            code, _, err = self.cli("drake", "private-caption")
        self.assertEqual(len(attempts), 1)
        self.assertNotEqual(code, 0)
        self.assertIn("write", err)
        self.assertNotIn("private-caption", err)
        self.assertNotIn("Traceback", err)

    def test_url_only_preserves_captions_without_image_download(self):
        self.seed_cache()
        with patch.dict(self.g, fetch_url=lambda *a, **k: self.fail("image fetch")):
            code, out, err = self.cli("drake", "", "a & b", "--url-only")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("/drake/_/a_~a_b.png", out)

    def test_remote_error_does_not_echo_captions_or_body(self):
        self.seed_cache()
        with patch.dict(self.g, fetch_url=lambda *a, **k: (_ for _ in ()).throw(
                OSError("private-caption-and-response-body"))):
            code, out, err = self.cli("drake", "private-caption-and-response-body")
        self.assertNotEqual(code, 0)
        self.assertNotIn("private-caption", out + err)
        self.assertNotIn("Traceback", err)

    def test_bounded_http_reads_ignore_lying_content_length(self):
        for advertised in (None, "1", "1000000"):
            with self.subTest(advertised=advertised):
                response = io.BytesIO(b"x" * 50)
                response.headers = {} if advertised is None else {
                    "Content-Length": advertised}
                self.network.side_effect = None
                self.network.return_value = response
                with self.assertRaises(ValueError):
                    self.call("fetch_url_in_process", FETCH_URL, max_bytes=20)

    def test_http_at_limit_succeeds(self):
        response = io.BytesIO(b"x" * 20)
        response.headers = {}
        self.network.side_effect = None
        self.network.return_value = response
        self.assertEqual(self.call("fetch_url_in_process", FETCH_URL, max_bytes=20),
                         b"x" * 20)

    def test_fetch_origin_validation_precedes_worker_launch(self):
        for url in (FETCH_URL, "HTTPS://API.MEMEGEN.LINK:443/images/test.png"):
            self.call("validate_fetch_url", url)
        forbidden = ["http://api.memegen.link/templates/", "https://localhost/",
                     "https://127.0.0.1/", "https://[::1]/", "https://10.0.0.1/",
                     "https://api.memegen.link.evil.invalid/", "https://other.invalid/",
                     "https://api.memegen.link:444/", "https://user@api.memegen.link/",
                     "file:///tmp/image.png", "https://[broken/",
                     "https://api.memegen.link/\nsecret"]
        for url in forbidden:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.call("fetch_url", url)
        self.process.assert_not_called()
        self.network.assert_not_called()

    def redirect_opener(self, locations, requests):
        class OfflineHTTPS(urllib.request.HTTPSHandler):
            def https_open(inner, request):
                requests.append(request.full_url)
                headers = Message()
                destination = locations.get(request.full_url)
                if destination:
                    code, target = destination
                    headers["Location"] = target
                else:
                    code = 200
                response = urllib.response.addinfourl(
                    io.BytesIO(b"image"), headers, request.full_url, code)
                response.msg = "offline"
                self.responses.append(response)
                return response
        self.network.side_effect = REAL_OPENER_OPEN
        return urllib.request.build_opener(OfflineHTTPS(),
                                           self.g["SafeRedirectHandler"]())

    def test_same_origin_redirects_and_supported_statuses(self):
        target = "https://api.memegen.link/final"
        for status in (301, 302, 303, 307, 308):
            for location in ("/final", target):
                with self.subTest(status=status, location=location):
                    requests = []
                    opener = self.redirect_opener({FETCH_URL: (status, location)},
                                                  requests)
                    with patch("urllib.request.build_opener", return_value=opener):
                        self.assertEqual(self.call("fetch_url_in_process", FETCH_URL),
                                         b"image")
                    self.assertEqual(requests, [FETCH_URL, target])

    def test_forbidden_redirects_never_request_destination(self):
        for target in ("http://api.memegen.link/image", "https://localhost/image",
                       "https://127.0.0.1/image", "https://[::1]/image",
                       "https://192.168.1.1/image", "https://other.invalid/image",
                       "https://api.memegen.link.evil.invalid/image",
                       "https://api.memegen.link:444/image",
                       "https://user@api.memegen.link/image", "file:///tmp/image"):
            with self.subTest(target=target):
                requests = []
                opener = self.redirect_opener({FETCH_URL: (302, target)}, requests)
                with patch("urllib.request.build_opener", return_value=opener):
                    with self.assertRaises((ValueError, urllib.error.HTTPError)):
                        self.call("fetch_url_in_process", FETCH_URL)
                self.assertEqual(requests, [FETCH_URL])
                self.assertTrue(all(response.closed for response in self.responses))

    def test_redirect_loops_and_hop_limit(self):
        requests = []
        opener = self.redirect_opener({FETCH_URL: (302, FETCH_URL)}, requests)
        with patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(urllib.error.HTTPError):
                self.call("fetch_url_in_process", FETCH_URL)
        self.assertLessEqual(len(requests), 6)
        requests = []
        urls = [FETCH_URL] + [f"https://api.memegen.link/hop-{i}" for i in range(6)]
        opener = self.redirect_opener(
            {start: (302, end) for start, end in zip(urls, urls[1:])}, requests)
        with patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(urllib.error.HTTPError):
                self.call("fetch_url_in_process", FETCH_URL)
        self.assertEqual(len(requests), 6)
        self.assertTrue(all(response.closed for response in self.responses))

    def test_supervised_fetch_uses_private_stdin_and_bounded_output(self):
        url = "https://api.memegen.link/images/drake/private-caption.png"
        self.process.side_effect = None
        self.process.return_value = subprocess.CompletedProcess([], 0, b"image", b"")
        self.assertEqual(self.call("fetch_url", url, max_bytes=20), b"image")
        args, kwargs = self.process.call_args
        self.assertEqual(args[0], [sys.executable, str(SCRIPT.absolute()),
                                   "--fetch-worker"])
        self.assertNotIn("private-caption", repr(args))
        self.assertEqual(json.loads(kwargs["input"]), {"url": url, "max_bytes": 20})
        self.assertEqual(kwargs["timeout"], 30)
        self.process.return_value = subprocess.CompletedProcess([], 0, b"x" * 21, b"")
        with self.assertRaises(ValueError):
            self.call("fetch_url", url, max_bytes=20)

    def test_worker_errors_and_timeout_do_not_echo_private_data(self):
        self.seed_cache()
        cases = [subprocess.CompletedProcess([], 1, b"private-caption", b"private-caption"),
                 subprocess.TimeoutExpired("private-caption", 30,
                                           output=b"private-caption"),
                 OSError("private-caption")]
        for error in cases:
            with self.subTest(error=type(error).__name__):
                self.process.side_effect = error if isinstance(error, Exception) else None
                self.process.return_value = error
                code, out, err = self.cli("drake", "private-caption")
                self.assertNotEqual(code, 0)
                self.assertNotIn("private-caption", out + err)
                self.assertNotIn("Traceback", err)
                if isinstance(error, subprocess.TimeoutExpired):
                    self.assertIn("deadline", err)
                self.assertFalse((self.root / "out").exists())

    def test_supervised_deadline_kills_and_reaps_real_worker(self):
        for mode in ("headers", "body", "dribble"):
            with self.subTest(mode=mode):
                pid_file = self.root / (mode + ".pid")
                child = (
                    "import os, pathlib, sys, time\n"
                    f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
                    "sys.stdin.buffer.read()\n"
                    + ("sys.stdout.buffer.write(b'private-caption'); "
                       "sys.stdout.buffer.flush()\n" if mode == "body" else "")
                    + ("while True:\n time.sleep(.01)\n "
                       "sys.stdout.buffer.write(b'x'); sys.stdout.buffer.flush()\n"
                       if mode == "dribble" else "time.sleep(10)\n"))
                self.process.side_effect = lambda args, **kwargs: REAL_RUN(
                    [sys.executable, "-c", child], **kwargs)
                start = time.monotonic()
                with patch.dict(self.g, TRANSFER_TIMEOUT=0.2):
                    with self.assertRaisesRegex(ValueError, "deadline"):
                        self.call("fetch_url", FETCH_URL)
                self.assertLess(time.monotonic() - start, 2)
                self.assertTrue(pid_file.exists())
                if os.name == "posix":
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(pid_file.read_text()), 0)

    def test_http_worker_status_is_safe(self):
        self.seed_cache()
        self.process.side_effect = None
        self.process.return_value = subprocess.CompletedProcess([], 2, b"", b"404")
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.call("fetch_url", FETCH_URL)
        self.assertTrue(result.exception.closed)
        code, out, err = self.cli("drake", "private-caption")
        self.assertNotEqual(code, 0)
        self.assertIn("HTTP 404", err)
        self.assertNotIn("private-caption", out + err)

    def test_worker_protocol_validates_requests_and_preserves_safe_http_status(self):
        for request in ([], {"url": "https://localhost/", "max_bytes": 20},
                        {"url": FETCH_URL, "max_bytes": True},
                        {"url": FETCH_URL, "max_bytes": 21 * 1024 * 1024},
                        {"url": FETCH_URL, "max_bytes": 20, "extra": "private-caption"}):
            with self.subTest(request=request), patch.object(
                    sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))):
                with self.assertRaises(ValueError):
                    self.call("fetch_worker")
        self.network.assert_not_called()
        request = {"url": FETCH_URL, "max_bytes": 20}
        output, errors = io.BytesIO(), io.StringIO()
        with patch.object(sys, "stdin", SimpleNamespace(
                buffer=io.BytesIO(json.dumps(request).encode()))), patch.object(
                sys, "stdout", SimpleNamespace(buffer=output)), patch.dict(
                self.g, fetch_url_in_process=lambda *args: b"image"):
            self.call("fetch_worker")
        self.assertEqual(output.getvalue(), b"image")
        with patch.object(sys, "stdin", SimpleNamespace(
                buffer=io.BytesIO(json.dumps(request).encode()))), patch.dict(
                self.g, fetch_url_in_process=lambda *args: (_ for _ in ()).throw(
                    urllib.error.HTTPError("private-caption", 404, "private-caption", None, None))), \
                contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as result:
            self.call("fetch_worker")
        self.assertEqual(result.exception.code, 2)
        self.assertEqual(errors.getvalue(), "404")

    def test_failed_worker_refresh_preserves_cache(self):
        path = self.seed_cache()
        before = path.read_bytes()
        self.process.side_effect = subprocess.TimeoutExpired(
            "private-caption", 30, output=b"private-caption")
        code, out, err = self.cli("refresh")
        self.assertNotEqual(code, 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("private-caption", out + err)

    def test_duplicate_catalog_normalization_is_stable(self):
        first = dict(CATALOG[0], lines=1, source="first")
        second = dict(CATALOG[0], lines=2, source="second")
        tied = dict(second, source="tie")
        for records in ([first, second, tied], [second, first, tied]):
            with self.subTest(records=records):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    result = self.call("validate_templates", records)
                self.assertEqual(result, [second])
                self.assertIn("duplicate", stderr.getvalue())
                self.assertNotIn("Drake", stderr.getvalue())
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(self.call("validate_templates", CATALOG * 2), CATALOG)
        self.assertEqual(stderr.getvalue(), "")

    def test_duplicate_records_are_all_validated(self):
        for invalid in (dict(CATALOG[0], name="\x1b[31m"),
                        dict(CATALOG[0], lines=True)):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.call("validate_templates", CATALOG + [invalid])

    def test_duplicate_cache_and_refresh_are_normalized(self):
        duplicates = [dict(CATALOG[0], lines=1)] + CATALOG
        self.seed_cache(duplicates)
        code, out, err = self.cli("list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), CATALOG)
        self.assertIn("duplicate", err)
        with patch.dict(self.g, fetch_templates_from_api=lambda: duplicates):
            code, _, err = self.cli("refresh")
        self.assertEqual(code, 0)
        self.assertIn("duplicate", err)
        self.assertEqual(json.loads(self.g["CACHE_FILE"].read_text()), CATALOG)
        with contextlib.redirect_stderr(io.StringIO()):
            self.call("save_cache", duplicates)
        self.assertEqual(json.loads(self.g["CACHE_FILE"].read_text()), CATALOG)

    def test_cold_cache_render_survives_duplicate_catalog(self):
        duplicates = [dict(CATALOG[0], lines=1)] + CATALOG
        def fetch(url, **kwargs):
            return json.dumps(duplicates).encode() if "/templates/" in url else PNG
        with patch.dict(self.g, fetch_url=fetch):
            code, out, err = self.cli("drake", "first", "second")
        self.assertEqual(code, 0)
        self.assertEqual(Path(out.strip()).read_bytes(), PNG)
        self.assertIn("duplicate", err)
        self.assertEqual(json.loads(self.g["CACHE_FILE"].read_text()), CATALOG)

    def test_invalid_catalogs_fail_cleanly(self):
        invalid = [{"error": "private-caption"}, [None], [{}],
                   [{"id": "../bad", "name": "Bad", "lines": 2}],
                   [{"id": "bad?x", "name": "Bad", "lines": 2}],
                   [{"id": "bad", "name": "\x1b[31m", "lines": 2}]]
        invalid += [[dict(CATALOG[0], lines=value)]
                    for value in (True, "2", -1, 33, 2.5)]
        for value in invalid:
            with self.subTest(value=value):
                with patch.dict(self.g, fetch_templates_from_api=lambda: value):
                    code, _, err = self.cli("list")
                self.assertNotEqual(code, 0)
                self.assertNotIn("Traceback", err)
                self.assertNotIn("private-caption", err)
                self.assertFalse(self.g["CACHE_FILE"].exists())

    def test_invalid_cache_does_not_crash(self):
        self.seed_cache({"error": "invalid"})
        with patch.dict(self.g, fetch_templates_from_api=lambda: CATALOG):
            code, out, err = self.cli("list", "--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), CATALOG)

    def test_cache_size_limit(self):
        path = self.seed_cache()
        with patch.dict(self.g, MAX_TEMPLATE_BYTES=10):
            self.assertIsNone(self.call("load_cache"))
        self.assertTrue(path.exists())

    def test_bad_json_refresh_keeps_cache(self):
        path = self.seed_cache()
        before = path.read_bytes()
        with patch.dict(self.g, fetch_url=lambda *a, **k: b"not json"):
            code, _, err = self.cli("refresh")
        self.assertNotEqual(code, 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("Traceback", err)

    def test_invalid_schema_refresh_keeps_cache(self):
        path = self.seed_cache()
        before = path.read_bytes()
        with patch.dict(self.g, fetch_templates_from_api=lambda: {"bad": "data"}):
            code, _, _ = self.cli("refresh")
        self.assertNotEqual(code, 0)
        self.assertEqual(path.read_bytes(), before)

    def test_cache_replace_failure_preserves_previous_contents(self):
        path = self.seed_cache()
        before = path.read_bytes()
        with patch("os.replace", side_effect=PermissionError), \
                patch.dict(self.g, fetch_templates_from_api=lambda: []):
            code, _, err = self.cli("refresh")
        self.assertNotEqual(code, 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("Traceback", err)

    def test_valid_stale_cache_survives_network_failure(self):
        path = self.seed_cache()
        os.utime(path, (0, 0))
        with patch.dict(self.g, fetch_templates_from_api=lambda: (_ for _ in ()).throw(
                OSError("private-caption"))):
            code, out, err = self.cli("list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), CATALOG)
        self.assertIn("stale cache", err)
        self.assertNotIn("private-caption", err)

    def test_invalid_stale_cache_cannot_be_fallback(self):
        path = self.seed_cache({"bad": "data"})
        os.utime(path, (0, 0))
        with patch.dict(self.g, fetch_templates_from_api=lambda: (_ for _ in ()).throw(
                OSError("offline"))):
            code, _, _ = self.cli("list")
        self.assertNotEqual(code, 0)

    def test_image_container_checks(self):
        for ext, data in {"png": PNG, "gif": GIF, "jpg": JPEG, "webp": WEBP}.items():
            with self.subTest(ext=ext):
                self.assertTrue(self.call("validate_image_bytes", data, ext))
                self.assertFalse(self.call("validate_image_bytes", data[:8], ext))
                self.assertFalse(self.call("validate_image_bytes", data[:-2], ext))
        for ext, data in [("png", b"\x89PNG"), ("gif", b"GIFgarbage"),
                          ("webp", b"RIFF\x04\x00\x00\x00WAVE"),
                          ("webp", WEBP[:4] + struct.pack("<I", 9999) + WEBP[8:]),
                          ("jpg", PNG), ("png", b""), ("unknown", PNG)]:
            with self.subTest(ext=ext, data=data):
                self.assertFalse(self.call("validate_image_bytes", data, ext))

    def test_oversized_image_dimensions_rejected_for_every_format(self):
        makers = {"png": png_image, "jpg": jpeg_image, "gif": gif_image,
                  "webp": lambda w, h: webp_file(webp_bitstream(w, h))}
        for ext, make in makers.items():
            for width, height in ((4097, 1), (1, 4097), (4001, 4000),
                                  (20000, 20000), (0, 1), (1, 0)):
                with self.subTest(ext=ext, width=width, height=height):
                    self.assertFalse(self.call("validate_image_bytes", make(width, height), ext))
            for width, height in ((4096, 1), (1, 4096), (4000, 4000)):
                with self.subTest(ext=ext, width=width, height=height):
                    self.assertTrue(self.call("validate_image_bytes", make(width, height), ext))

    def test_oversized_response_never_opens_output(self):
        self.render_setup(png_image(20000, 20000))
        target = self.root / "huge.png"
        code, _, err = self.cli("drake", "private-caption", "--out", str(target))
        self.assertNotEqual(code, 0)
        self.assertFalse(target.exists())
        self.assertNotIn("private-caption", err)

    def test_requested_dimensions_rejected_before_any_network(self):
        for options in (("--width", "0"), ("--width", "-1"),
                        ("--width", "4097"), ("--height", "0"),
                        ("--height", "4097"),
                        ("--width", "4001", "--height", "4000")):
            with self.subTest(options=options):
                code, out, err = self.cli("drake", "private-caption", *options)
                self.assertNotEqual(code, 0)
                self.assertIn("dimension", err)
                self.assertNotIn("private-caption", out + err)
        self.network.assert_not_called()
        self.process.assert_not_called()
        self.assertFalse(self.g["CACHE_FILE"].exists())

    def test_animation_frame_and_composited_pixel_budgets(self):
        for ext, make in (("png", png_image), ("gif", gif_image),
                          ("webp", webp_extended)):
            for width, height, frames, valid in ((1, 1, 100, True), (1, 1, 101, False),
                                                (4000, 4000, 2, True),
                                                (4000, 4000, 3, False)):
                with self.subTest(ext=ext, frames=frames, canvas=(width, height)):
                    self.assertEqual(self.call("validate_image_bytes",
                                               make(width, height, frames=frames), ext), valid)
        for ext, make in (("gif", gif_image), ("webp", webp_extended)):
            with self.subTest(ext=ext, patches=True):
                image = make(4000, 4000, frames=3, rectangle=(0, 0, 1, 1))
                self.assertFalse(self.call("validate_image_bytes", image, ext))

    def test_apng_count_order_and_frame_bounds(self):
        valid = png_image(1, 1, frames=2)
        self.assertTrue(self.call("validate_image_bytes", valid, "png"))
        self.assertTrue(self.call("validate_image_bytes",
                                  png_image(1, 1, frames=2, default_frame=False), "png"))
        self.assertFalse(self.call("validate_image_bytes",
                                   png_image(1, 1, frames=2, declared=3), "png"))
        # The static fallback is an additional full canvas, not an animation frame.
        self.assertFalse(self.call("validate_image_bytes", png_image(
            4000, 4000, frames=2, default_frame=False), "png"))
        offset = valid.index(b"fcTL") - 4
        length = int.from_bytes(valid[offset:offset + 4], "big")
        payload = valid[offset + 8:offset + 8 + length]
        for position, replacement in ((0, struct.pack(">I", 1)),
                                      (4, struct.pack(">I", 2)),
                                      (12, struct.pack(">I", 1))):
            altered = payload[:position] + replacement + payload[position + 4:]
            data = valid[:offset] + png_chunk(b"fcTL", altered) + valid[offset + length + 12:]
            self.assertFalse(self.call("validate_image_bytes", data, "png"))
        static = png_image(1, 1)
        self.assertFalse(self.call("validate_image_bytes",
                                   static[:33] + png_chunk(b"fcTL", payload) + static[33:], "png"))

    def test_gif_descriptor_and_animation_bounds(self):
        for rectangle in ((0, 0, 0, 1), (0, 0, 2, 1), (1, 0, 1, 1)):
            with self.subTest(rectangle=rectangle):
                self.assertFalse(self.call("validate_image_bytes", gif_image(
                    1, 1, rectangle=rectangle), "gif"))
        self.assertFalse(self.call("validate_image_bytes", gif_image(1, 1, frames=0), "gif"))
        # A comma in comment metadata is not an image descriptor.
        comment = GIF[:19] + b"\x21\xfe\x08,comment\0;"
        self.assertFalse(self.call("validate_image_bytes", comment, "gif"))

    def test_webp_canvas_frame_and_bitstream_dimensions_agree(self):
        for lossless in (False, True):
            self.assertTrue(self.call("validate_image_bytes", webp_file(
                webp_bitstream(1, 1, lossless=lossless)), "webp"))
            self.assertFalse(self.call("validate_image_bytes", webp_file(
                webp_bitstream(4097, 1, lossless=lossless)), "webp"))
        for frames in (None, 1):
            with self.subTest(frames=frames):
                self.assertFalse(self.call("validate_image_bytes", webp_extended(
                    1, 1, frames=frames, bitstream_size=(2, 1)), "webp"))
        self.assertFalse(self.call("validate_image_bytes", webp_extended(
            1, 1, frames=1, rectangle=(2, 0, 1, 1)), "webp"))
        self.assertFalse(self.call("validate_image_bytes", webp_extended(
            1, 1, frames=0), "webp"))
        data = webp_extended(1, 1)
        self.assertFalse(self.call("validate_image_bytes",
                                   webp_file(data[12:], data[12:30]), "webp"))
        # Lossless version bits and lossy key-frame signature must be recognized.
        lossless = webp_bitstream(1, 1, lossless=True)
        self.assertFalse(self.call("validate_image_bytes", webp_file(
            lossless[:12] + bytes([lossless[12] | 0xe0]) + lossless[13:]), "webp"))
        lossy = webp_bitstream(1, 1)
        self.assertFalse(self.call("validate_image_bytes", webp_file(
            lossy[:11] + b"bad" + lossy[14:]), "webp"))
        self.assertFalse(self.call("validate_image_bytes", webp_file(
            lossy[:8] + bytes([lossy[8] | 8]) + lossy[9:]), "webp"))

    def test_jpeg_conflicting_frame_headers_rejected(self):
        offset = JPEG.index(b"\xff\xc0")
        length = int.from_bytes(JPEG[offset + 2:offset + 4], "big") + 2
        duplicate = JPEG[:offset] + JPEG[offset:offset + length] + JPEG[offset:]
        self.assertFalse(self.call("validate_image_bytes", duplicate, "jpg"))

    def test_png_empty_idat_still_obeys_chunk_ordering(self):
        static = png_image(1, 1)
        empty = png_chunk(b"IDAT", b"")
        self.assertTrue(self.call("validate_image_bytes", static[:33] + empty + static[33:], "png"))
        self.assertFalse(self.call("validate_image_bytes", static[:33] + empty
                                   + png_chunk(b"tEXt", b"comment") + static[33:], "png"))
        animated = png_image(1, 1, frames=1)
        self.assertFalse(self.call("validate_image_bytes",
                                   animated[:33] + empty + animated[33:], "png"))

    def test_png_corrupt_chunk_is_rejected(self):
        corrupt = PNG[:-13] + bytes([PNG[-13] ^ 1]) + PNG[-12:]
        self.assertFalse(self.call("validate_image_bytes", corrupt, "png"))

    def test_caption_count_boundaries(self):
        for count in (0, 32):
            self.seed_cache([dict(CATALOG[0], lines=count)])
            code, out, err = self.cli("drake", "--url-only")
            self.assertEqual((code, err), (0, ""))
            self.assertIn("/drake/", out)
        code, _, err = self.cli("drake", *(["caption"] * 33), "--url-only")
        self.assertNotEqual(code, 0)
        self.assertNotIn("Traceback", err)

    def test_download_calls_only_bounded_reads(self):
        class Response(io.BytesIO):
            headers = {}
            def read(self, size=-1):
                if not 0 < size <= 65536:
                    raise AssertionError("unbounded read")
                return super().read(size)
        self.network.side_effect = None
        self.network.return_value = Response(b"image")
        self.assertEqual(self.call("fetch_url_in_process", FETCH_URL), b"image")

    def test_help_alias(self):
        for flag in ("help", "--help"):
            code, out, err = self.cli(flag)
            self.assertEqual((code, err), (0, ""))
            self.assertIn("usage:", out)

    def test_list_search_and_unknown_template(self):
        self.seed_cache()
        for argv in (("list", "--json"), ("search", "Drake", "--json")):
            code, out, err = self.cli(*argv)
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(json.loads(out), CATALOG)
        code, _, err = self.cli("drak", "--url-only")
        self.assertNotEqual(code, 0)
        self.assertIn("drake", err)

    def test_refresh_valid_catalog(self):
        with patch.dict(self.g, fetch_templates_from_api=lambda: CATALOG):
            code, _, err = self.cli("refresh")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.call("load_cache"), CATALOG)

    def test_escaping_and_options(self):
        self.assertEqual(self.call("encode_segment", "_ - ? & % # / \\ < > \"\n"),
                         "___--_~q_~a_~p_~h_~s_~b_~l_~g_''~n")
        url = self.call("build_image_url", "drake", ["a", ""], ext="webp",
                        width=800, height=600, font="comic")
        self.assertIn("/a/_.webp?width=800&height=600&font=comic", url)

    def test_relocated_absolute_invocation_from_unrelated_cwd(self):
        self.process.side_effect = REAL_RUN
        skill = self.root / "skill with spaces" / "scripts"
        skill.mkdir(parents=True)
        moved = skill / "meme.py"
        shutil.copyfile(SCRIPT, moved)
        env = dict(os.environ, HOME=str(self.root), PYTHONDONTWRITEBYTECODE="1")
        for flag in ("help", "--help"):
            result = subprocess.run([sys.executable, str(moved), flag], cwd=self.root,
                                    env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage:", result.stdout)
        cache = self.root / ".cache" / "meme-generator" / "templates.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps(CATALOG))
        result = subprocess.run(
            [sys.executable, str(moved), "drake", "first", "", "--url-only"],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/drake/first/_.png", result.stdout)


if __name__ == "__main__":
    unittest.main()
