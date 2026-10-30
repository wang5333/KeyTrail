"""DPAPI on Windows; AES-256-GCM plus a private local key file on Linux.

The Linux key file is NOT a keyring or full-disk encryption replacement. A
backup containing ciphertext AND key can decrypt the result. The entire private
directory belongs in an encrypted offline backup, never in a plan export.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import secrets
import stat

MAGIC = b"KeyTrail-AESGCM-v1\x00"


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def private_directory(directory):
    path = Path(directory)
    if os.name != "nt" and path.is_symlink():
        raise ValueError("Private directory must not be a symbolic link")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        if path.stat().st_uid != os.getuid():
            raise PermissionError("Private directory belongs to another user")
        path.chmod(0o700)
    return path


def result_path(directory):
    return Path(directory) / ("puzzle71-result.dpapi" if os.name == "nt"
                              else "puzzle71-result.aesgcm")


def _local_key(directory, create):
    path = private_directory(directory) / "result-protection.key"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if not create:
            raise RuntimeError("Result protection key is missing; restore the private backup") from None
        # Only the instance-lock owner writes results. O_EXCL also prevents
        # accidental key replacement if the protection API is called directly.
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            return _local_key(directory, False)
        with os.fdopen(fd, "wb") as stream:
            stream.write(secrets.token_bytes(32))
            stream.flush()
            os.fsync(stream.fileno())
        return _local_key(directory, False)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise PermissionError("Protection key must be a private, owner-only regular file (0600)")
        key = stream.read(33)
    if len(key) != 32:
        raise ValueError("Invalid result protection key; restore the private backup")
    return key


def protect(data, decrypt=False, *, directory=None):
    if os.name != "nt":
        if directory is None:
            raise ValueError("A private result directory is required on Linux")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.exceptions import InvalidTag
        if decrypt and (not data.startswith(MAGIC) or len(data) < len(MAGIC) + 28):
            raise ValueError("Unsupported protected result; Windows DPAPI files need the original Windows account")
        cipher = AESGCM(_local_key(directory, create=not decrypt))
        if decrypt:
            offset = len(MAGIC)
            try:
                return cipher.decrypt(data[offset:offset+12], data[offset+12:], MAGIC)
            except InvalidTag:
                raise ValueError("Protected result authentication failed; restore the correct private backup") from None
        nonce = secrets.token_bytes(12)
        return MAGIC + nonce + cipher.encrypt(nonce, data, MAGIC)
    buffer = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt32.CryptUnprotectData(ctypes.byref(incoming), None, None, None,
                                       None, 1, ctypes.byref(outgoing))
    else:
        ok = crypt32.CryptProtectData(ctypes.byref(incoming), "Puzzle71 local result",
                                     None, None, None, 1, ctypes.byref(outgoing))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel32.LocalFree(outgoing.data)
