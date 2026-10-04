"""CFString conversion, bounded allocation, and fake AudioObject boundaries."""
import ctypes
import ctypes.util
import sys
import unittest
from unittest.mock import MagicMock, patch

import airpods_say as say


class TestCFStringBoundary(unittest.TestCase):
    def fake_frameworks(self, maximum=30, converts=True):
        ca, cf = MagicMock(), MagicMock()
        ref = 0x7fff12345678
        calls = []

        def audio(*args):
            calls.append(True)
            if len(calls) == 1:
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_uint32))[0] = 42
            else:
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_void_p))[0] = ref
            return 0

        ca.AudioObjectGetPropertyData.side_effect = audio
        cf.CFStringGetLength.return_value = 10
        cf.CFStringGetMaximumSizeForEncoding.return_value = maximum

        def convert(ref, buf, size, encoding):
            buf.value = 'AirPods é'.encode()
            return converts

        cf.CFStringGetCString.side_effect = convert
        return ca, cf

    def run_conversion(self, maximum=30, converts=True):
        ca, cf = self.fake_frameworks(maximum, converts)
        allocate = ctypes.create_string_buffer

        def safe_allocate(size):
            self.assertLessEqual(size, 128, 'unsafe pointer-sized allocation')
            self.assertGreater(size, 0)
            return allocate(size)

        with patch.object(say, '_load_coreaudio', return_value=ca), \
                patch('ctypes.util.find_library', return_value='fake-cf'), \
                patch('ctypes.cdll.LoadLibrary', return_value=cf), \
                patch('ctypes.create_string_buffer', side_effect=safe_allocate):
            try:
                return say._get_default_output_device_name_coreaudio()
            finally:
                cf.CFRelease.assert_called_once()
                cf.CFStringGetLength.assert_called_once()
                cf.CFStringGetMaximumSizeForEncoding.assert_called_once_with(
                    10, 0x08000100)
                self.assertEqual(cf.CFStringGetLength.restype, ctypes.c_long)
                self.assertEqual(cf.CFStringGetMaximumSizeForEncoding.argtypes,
                                 [ctypes.c_long, ctypes.c_uint32])

    def test_high_address_uses_character_count_and_unicode(self):
        self.assertEqual(self.run_conversion(), 'AirPods é')

    def test_invalid_sizes_and_conversion_failure_release(self):
        for maximum, converts in ((-1, True), (sys.maxsize, True), (30, False)):
            with self.subTest(maximum=maximum, converts=converts):
                with self.assertRaises(say.CoreAudioError):
                    self.run_conversion(maximum, converts)

    @unittest.skipUnless(sys.platform == 'darwin', 'CoreFoundation requires macOS')
    def test_real_cfstring_with_fake_audioobject_calls(self):
        cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library('CoreFoundation'))
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                               ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        ref = cf.CFStringCreateWithCString(None, 'AirPods é'.encode(), 0x08000100)
        self.assertTrue(ref)
        ca = MagicMock()
        calls = []

        def audio(*args):
            calls.append(True)
            typ, val = (ctypes.c_uint32, 42) if len(calls) == 1 else (ctypes.c_void_p, ref)
            ctypes.cast(args[-1], ctypes.POINTER(typ))[0] = val
            return 0

        ca.AudioObjectGetPropertyData.side_effect = audio
        with patch.object(say, '_load_coreaudio', return_value=ca):
            self.assertEqual(say._get_default_output_device_name_coreaudio(), 'AirPods é')
