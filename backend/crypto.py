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


# A note of which backend the key was last written to.
#
# Without it, "the keyring read returned nothing" and "no key has ever existed"
# are the same event — so a keyring that was briefly unreachable (a locked
# Keychain, a backend that had not started yet) looked like a first run, a fresh
# key was minted, and the stored ciphertext became permanently undecryptable
# (NEXT.md 2.7). Recoverable by re-pasting the API key, but it failed quietly
# toward data loss, which is the worst direction.
#
# Deliberately a plain file next to the fallback key rather than a database row:
# `db` depends on this module, so it cannot be the other way round, and this must
# work before the database is readable.
BACKEND_NOTE = FALLBACK_DIR / "key-backend"

KEYRING = "keyring"
KEYFILE = "file"


def _remember_backend(which: str) -> None:
    try:
        FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        BACKEND_NOTE.write_text(which, encoding="ascii")
    except OSError as exc:
        # Not fatal: the next read simply falls back to the old behaviour.
        log.info("could not record the key backend (%s)", type(exc).__name__)


def _remembered_backend() -> str | None:
    try:
        return BACKEND_NOTE.read_text(encoding="ascii").strip() or None
    except OSError:
        return None


def _from_keyring() -> str | None:
    """The key from the OS keyring, or None if it is not there.

    Raises `CryptoError` when the keyring is *expected* to hold the key but
    cannot be reached — see `encryption_key`. Returning None in that case is
    what caused a fresh key to be minted over a perfectly good one.
    """
    try:
        import keyring

        return keyring.get_password(SERVICE, ENTRY)
    except Exception as exc:  # noqa: BLE001 - any keyring backend failure is non-fatal
        log.info("keyring unavailable (%s); using the key file", type(exc).__name__)
        if _remembered_backend() == KEYRING:
            raise CryptoError(
                "The encryption key is held in this computer's keyring, and the "
                "keyring cannot be read right now "
                f"({type(exc).__name__}). Your saved API key is intact — but it "
                "cannot be unlocked until the keyring is available again. On macOS "
                "unlock the login Keychain; on Windows sign in again. Nothing has "
                "been changed or replaced."
            ) from exc
        return None


def _to_keyring(value: str) -> bool:
    try:
        import keyring

        keyring.set_password(SERVICE, ENTRY, value)
    except Exception as exc:  # noqa: BLE001
        log.info("could not store key in the keyring (%s)", type(exc).__name__)
        return False
    _remember_backend(KEYRING)
    return True


def _from_file() -> str | None:
    if not FALLBACK_FILE.exists():
        return None
    return FALLBACK_FILE.read_text(encoding="ascii").strip() or None


def _to_file(value: str) -> None:
    FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    FALLBACK_FILE.write_text(value, encoding="ascii")
    # Owner read/write only. On Windows this is a no-op but harmless.
    FALLBACK_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    _remember_backend(KEYFILE)


def encryption_key() -> bytes:
    """Fetch the key, creating one on first use.

    **A new key is minted only when there is genuinely no old one.** This used to
    treat any empty keyring read as "no key exists", so a keyring that was
    momentarily unreachable led to a fresh key and a stored API key that could
    never be decrypted again (NEXT.md 2.7). `_from_keyring` now refuses rather
    than returning None when the keyring is the recorded home of the key.
    """
    existing = _from_keyring() or _from_file()
    if existing:
        return existing.encode("ascii")

    remembered = _remembered_backend()
    if remembered == KEYFILE and not FALLBACK_FILE.exists():
        # The file was the recorded home and it is gone — deleted, or on a drive
        # that has not mounted. Minting a replacement would quietly orphan the
        # stored API key; saying so lets the operator restore or re-enter it.
        raise CryptoError(
            f"The encryption key file is missing ({FALLBACK_FILE}). Any saved API "
            f"key cannot be decrypted without it. Restore the file from a backup, "
            f"or delete the saved key in Settings and paste it again."
        )

    created = Fernet.generate_key().decode("ascii")
    if not _to_keyring(created):
        _to_file(created)
    return created.encode("ascii")


def key_location() -> str:
    """Where the key actually lives, for the Settings screen."""
    try:
        if _from_keyring():
            return "OS keyring"
    except CryptoError:
        return "OS keyring (currently unreachable)"
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
