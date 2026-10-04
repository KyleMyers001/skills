"""Neutral player and loopback HTTP fixtures for offline AirPods tests."""
import contextlib
import http.server
import io
import threading


class _Player:
    """State changes follow stdin EOF/termination, not poll call counts."""

    def __init__(self, eof_status=0):
        self.returncode = None
        self.eof_status = eof_status
        self.audio = bytearray()
        self.closed = False
        self.reaped = False
        self.killed = False
        self.on_write = None
        self.stdin = self
        self.stderr = io.BytesIO()

    def fileno(self):
        return 9999  # os.set_blocking is patched; no real player is launched

    def write(self, data):
        if self.on_write:
            self.on_write(data)
        self.audio.extend(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True
        if self.returncode is None:
            self.returncode = self.eof_status

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.reaped = True
        return self.returncode


@contextlib.contextmanager
def loopback_server(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(**kwargs):
    import airpods_say

    options = dict(api_key="dummy-key", text="Hello", voice_id="test-voice",
                   model_id="eleven_v4", output_format="mp3_44100_128",
                   timeout=1, debug=False)
    options.update(kwargs)
    return airpods_say.stream_tts(**options)
