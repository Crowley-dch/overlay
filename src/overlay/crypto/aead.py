from __future__ import annotations

import os
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import (
    AESGCM,
    ChaCha20Poly1305,
)

AeadAlgorithm = Literal["aes-gcm", "chacha20poly1305"]

NONCE_LEN = 12
TAG_LEN = 16
SALT_LEN = 4


class AeadError(Exception):
    pass


class AuthenticationError(AeadError):
    pass


class ReplayError(AeadError):
    pass


def _make_aead(algorithm: AeadAlgorithm, key: bytes):
    if len(key) != 32:
        raise ValueError("key должен быть 32 байта")
    if algorithm == "aes-gcm":
        return AESGCM(key)
    if algorithm == "chacha20poly1305":
        return ChaCha20Poly1305(key)
    raise ValueError(f"Неизвестный алгоритм: {algorithm}")


class AeadSession:
    def __init__(
        self,
        key: bytes,
        *,
        algorithm: AeadAlgorithm = "chacha20poly1305",
        salt: bytes | None = None,
    ) -> None:
        self._aead = _make_aead(algorithm, key)
        self._algorithm: AeadAlgorithm = algorithm
        self._salt = salt if salt is not None else os.urandom(SALT_LEN)
        if len(self._salt) != SALT_LEN:
            raise ValueError(f"salt должен быть {SALT_LEN} байта")
        self._send_counter = 0
        self._recv_max_seen = -1

    @property
    def algorithm(self) -> AeadAlgorithm:
        return self._algorithm

    @property
    def salt(self) -> bytes:
        return self._salt

    @property
    def send_counter(self) -> int:
        return self._send_counter

    def _nonce(self, counter: int) -> bytes:
        if counter < 0 or counter >= 1 << 64:
            raise AeadError("counter вне допустимого диапазона")
        return self._salt + counter.to_bytes(8, "big")

    def encrypt(self, plaintext: bytes, aad: bytes = b"") -> tuple[int, bytes]:
        counter = self._send_counter
        nonce = self._nonce(counter)
        try:
            ciphertext = self._aead.encrypt(nonce, plaintext, aad)
        except Exception as e:
            raise AeadError(f"Ошибка шифрования: {e}") from e
        self._send_counter += 1
        return counter, ciphertext

    def decrypt(
        self,
        counter: int,
        ciphertext: bytes,
        aad: bytes = b"",
    ) -> bytes:
        if counter <= self._recv_max_seen:
            raise ReplayError(
                f"counter {counter} <= уже виденного {self._recv_max_seen}"
            )
        nonce = self._nonce(counter)
        try:
            plaintext = self._aead.decrypt(nonce, ciphertext, aad)
        except InvalidTag as e:
            raise AuthenticationError(
                "Не удалось проверить целостность"
            ) from e
        except Exception as e:
            raise AeadError(f"Ошибка расшифровки: {e}") from e
        self._recv_max_seen = counter
        return plaintext