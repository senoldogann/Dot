"""Looks the clipboard's service up and prints what macOS answered (0: found, 1100: refused by a sandbox profile),
asking the service for nothing: no test reads or writes the clipboard of the Mac it runs on (item 534)."""

import ctypes

lib = ctypes.CDLL(None)
lookup = lib.bootstrap_look_up
lookup.argtypes = [ctypes.c_uint, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint)]
lookup.restype = ctypes.c_int
port = ctypes.c_uint(0)
bootstrap = ctypes.c_uint.in_dll(lib, "bootstrap_port")
print(lookup(bootstrap.value, b"com.apple.pasteboard.1", ctypes.byref(port)))
