"""Encryption for the one thing here with direct monetary value: the API key.

SECURITY.md requires keys encrypted at rest and never returned to the browser in
plaintext. This module owns the first half.

**Where the encryption key lives.** The OS keyring first — Keychain on macOS,
Credential Locker on Windows — because that is the one place designed for it. If
the keyring is unavailable (a headless box, a locked-down profile) it falls back
to a file in the user's home directory with owner-only permissions. Never the
repo, never `.env`, never anywhere a backup or a git add could sweep it up.

**Why not just put the key in `.env`.** Because `.env` gets copied to a USB stick
when someone moves the app to another machine, pasted into a chat when something
breaks, and included in a folder backup. An encrypted database plus a key held by
the OS survives all three.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

SERVICE = "focus-toolkit"
ENTRY = "db-encryption-key"

# Used only when the OS keyring cannot be reached.
FALLBACK_DIR = Path(os.getenv("FOCUS_KEY_DIR", Path.home() / ".focus-toolkit"))
FALLBACK_FILE = FALLBACK_DIR / "encryption.key"


class CryptoError(RuntimeError):
    """The encryption key is unavailable or the data cannot be read."""


def _from_keyring() -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, ENTRY)
    except Exception as exc:  # noqa: BLE001 - any keyring backend failure is non-fatal
        log.info("keyring unavailable (%s); using the key file", type(exc).__name__)
        return None


def _to_keyring(value: str) -> bool:
    try:
        import keyring

        keyring.set_password(SERVICE, ENTRY, value)
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("could not store key in the keyring (%s)", type(exc).__name__)
        return False


def _from_file() -> str | None:
    if not FALLBACK_FILE.exists():
        return None
    return FALLBACK_FILE.read_text(encoding="ascii").strip() or None


def _to_file(value: str) -> None:
    FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    FALLBACK_FILE.write_text(value, encoding="ascii")
    # Owner read/write only. On Windows this is a no-op but harmless.
    FALLBACK_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)


def encryption_key() -> bytes:
    """Fetch the key, creating one on first use."""
    existing = _from_keyring() or _from_file()
    if existing:
        return existing.encode("ascii")

    created = Fernet.generate_key().decode("ascii")
    if not _to_keyring(created):
        _to_file(created)
    return created.encode("ascii")


def key_location() -> str:
    """Where the key actually lives, for the Settings screen."""
    if _from_keyring():
        return "OS keyring"
    if FALLBACK_FILE.exists():
        return str(FALLBACK_FILE)
    return "not created yet"


def encrypt(plaintext: str) -> bytes:
    return Fernet(encryption_key()).encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    try:
        return Fernet(encryption_key()).decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError(
            "The stored key could not be decrypted. If the encryption key was "
            "lost or replaced, re-enter the API key in Settings."
        ) from exc


def hint(plaintext: str) -> str:
    """The only form of a key that may ever reach the browser.

    Four characters is enough for the operator to tell two keys apart and far
    too little to be worth anything to anyone else.
    """
    tail = plaintext.strip()[-4:]
    return f"…{tail}" if tail else "…"
