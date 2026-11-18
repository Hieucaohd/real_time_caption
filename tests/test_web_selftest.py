"""The frozen smoke test must resolve Uvicorn's configured backend, not a private path."""
import sys
import types
import unittest
from unittest.mock import patch

import uvicorn.config
from uvicorn.importer import import_from_string, ImportFromStringError
from web_api.selftest import check_server_protocol


class WebSelftestTests(unittest.TestCase):
    def test_installed_protocol_loads_without_console_streams(self):
        expected = import_from_string(uvicorn.config.WS_PROTOCOLS['websockets-sansio'])
        with patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None):
            actual = check_server_protocol()
        self.assertIs(actual, expected)

    def test_follows_uvicorn_mapping_when_implementation_moves(self):
        module = types.ModuleType('_rtc_test_websocket_backend')
        module.Protocol = type('Protocol', (), {})
        with patch.dict(sys.modules, {module.__name__: module}), \
             patch.dict(uvicorn.config.WS_PROTOCOLS, {'websockets-sansio': module.__name__ + ':Protocol'}):
            self.assertIs(check_server_protocol(), module.Protocol)

    def test_missing_packaged_backend_still_fails_the_check(self):
        with patch.dict(uvicorn.config.WS_PROTOCOLS, {'websockets-sansio': '_rtc_missing_backend:Protocol'}):
            with self.assertRaises(ImportFromStringError):
                check_server_protocol()


if __name__ == '__main__':
    unittest.main()
