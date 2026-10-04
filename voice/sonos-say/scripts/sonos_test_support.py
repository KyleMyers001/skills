"""Neutral speaker, snapshot, configuration, and loopback test fixtures."""
import contextlib
import http.server
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock


TEST_CONFIG = {
    "ELEVENLABS_VOICE_ID": "test_voice_123",
    "SONOS_ROOM": "Test Room",
    "ELEVENLABS_KEYCHAIN_SERVICE": "test-tts-service",
    "ELEVENLABS_KEYCHAIN_ACCOUNT": "test-tts-account",
}


class FakeSpeaker:
    is_visible = True
    is_satellite = False
    has_satellites = False
    ip_address = '192.0.2.2'

    def __init__(self, uid='target'):
        self.uid = uid
        self.volume = 30
        self.mute = True
        self.uri = 'http://example.invalid/radio'
        self.state = 'STOPPED'
        self.zone_group_state = SimpleNamespace(has_subscriptions=False,
                                               clear_cache=lambda: None)
        self.group = SimpleNamespace(coordinator=self, members=[self])
        self.avTransport = SimpleNamespace(GetMediaInfo=self.media,
                                           SetAVTransportURI=MagicMock(side_effect=self.set_uri))
        self.play_uri = MagicMock(side_effect=self.play)
        self.join = MagicMock(side_effect=self.regroup)
        self.unjoin = MagicMock(side_effect=self.isolate)
        self.stop = MagicMock(side_effect=lambda: setattr(self, 'state', 'STOPPED'))
        self.before_play = None

    @property
    def is_coordinator(self):
        return self.group.coordinator is self

    def media(self, args):
        return {'CurrentURI': self.uri, 'CurrentURIMetaData': ''}

    def set_uri(self, args):
        self.uri = dict(args)['CurrentURI']

    def play(self, uri, **kwargs):
        if self.before_play:
            self.before_play()
        self.uri, self.state = uri, 'PLAYING'

    def get_current_track_info(self):
        return {'uri': self.uri}

    def get_current_transport_info(self):
        return {'current_transport_state': self.state}

    def isolate(self):
        coord = self.group.coordinator
        coord.group = SimpleNamespace(coordinator=coord, members=[coord])
        self.group = SimpleNamespace(coordinator=self, members=[self])

    def regroup(self, coord):
        self.group = coord.group = SimpleNamespace(coordinator=coord, members=[coord, self])


class FakeSnapshot:
    def __init__(self, speaker):
        self.device = speaker
        self.restores = 0
        self.is_playing_queue = False
        self.is_playing_cloud_queue = False

    def snapshot(self):
        self.media_uri = self.device.uri
        self.volume, self.mute = self.device.volume, self.device.mute
        self.transport_state = self.device.state

    def restore(self, fade=False):
        self.restores += 1
        self.device.uri = self.media_uri
        self.device.volume, self.device.mute = self.volume, self.mute
        self.device.state = self.transport_state


@contextlib.contextmanager
def loopback_server(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
