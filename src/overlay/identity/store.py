from __future__ import annotations

import os
from pathlib import Path

from overlay.identity.keypair import Identity

IDENTITY_FILE = "identity.pem"


class IdentityStore:
    def __init__(self, state_dir: str | os.PathLike) -> None:
        self.state_dir = Path(state_dir)
        self.identity_path = self.state_dir / IDENTITY_FILE

    def exists(self) -> bool:
        return self.identity_path.exists()

    def load_or_create(self) -> Identity:
        if self.exists():
            return self.load()
        identity = Identity.generate()
        self.save(identity)
        return identity

    def load(self) -> Identity:
        if not self.exists():
            raise FileNotFoundError(
                f"Файл идентичности не найден: {self.identity_path}"
            )
        data = self.identity_path.read_bytes()
        return Identity.from_pem(data)

    def save(self, identity: Identity) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        data = identity.to_private_pem()
        tmp = self.identity_path.with_suffix(".pem.tmp")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.identity_path)