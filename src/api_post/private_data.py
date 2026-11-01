"""Windows DPAPI, current-user encryption for imported source documents."""

import ctypes
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def transform(data: bytes, decrypt=False) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [
            ctypes.POINTER(Blob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(Blob),
        ]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [
            ctypes.POINTER(Blob),
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(Blob),
        ]
        ok = fn(
            ctypes.byref(source),
            "ReqDock private import",
            None,
            None,
            None,
            1,
            ctypes.byref(result),
        )
    if not ok:
        raise OSError("Windows DPAPI operation failed")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)
