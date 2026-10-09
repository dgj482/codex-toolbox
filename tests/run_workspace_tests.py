"""Run unit tests in a workspace runtime that does not bundle pywebview."""
import sys
import types
import unittest

try:
    import webview  # noqa: F401
except ModuleNotFoundError:
    sys.modules["webview"] = types.ModuleType("webview")
try:
    import websocket  # noqa: F401
except ModuleNotFoundError:
    websocket_stub = types.ModuleType("websocket")
    websocket_stub.create_connection = None
    websocket_stub.WebSocketTimeoutException = TimeoutError
    sys.modules["websocket"] = websocket_stub

arguments = sys.argv[1:]
portable = "--portable" in arguments
arguments = [item for item in arguments if item != "--portable"]
suite = (unittest.defaultTestLoader.loadTestsFromNames(arguments)
         if arguments else unittest.defaultTestLoader.discover("tests"))
if portable:
    excluded = {
        "test_surface_translation.SurfaceTranslationTests.test_translation_launcher_has_valid_powershell_syntax",
        "test_toolbox_core.ToolboxCoreTests.test_provider_file_contains_only_dpapi_ciphertext",
    }

    def flatten(items):
        for item in items:
            if isinstance(item, unittest.TestSuite):
                yield from flatten(item)
            else:
                yield item

    suite = unittest.TestSuite(test for test in flatten(suite) if test.id() not in excluded)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
