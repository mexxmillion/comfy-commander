"""Set or reset the Tensor Master admin password from the host PC.

Run: .venv312\\Scripts\\python.exe -m backend.app.password_cli

Only someone with access to this PC's user profile can run it. Resetting signs
out every device, because the identity store revokes all saved device sessions.
"""

from __future__ import annotations

import getpass
import json
import os
import sys

from .auth import MIN_PASSWORD_LENGTH, password_record
from .config import get_settings
from .identity import authority


def main() -> int:
    path = get_settings().token_file.with_name("admin_password.json")
    action = "Reset" if path.exists() else "Create"
    print(f"{action} the Tensor Master admin password ({path})")
    password = getpass.getpass(f"New password (at least {MIN_PASSWORD_LENGTH} characters): ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Too short: use at least {MIN_PASSWORD_LENGTH} characters. Nothing changed.")
        return 1
    if getpass.getpass("Repeat password: ") != password:
        print("Passwords do not match. Nothing changed.")
        return 1
    try:
        identity = authority(get_settings().token_file)
    except (OSError, RuntimeError):
        print("Saved API token is unavailable. Restore it before changing the password. Nothing changed.")
        return 1
    record = password_record(password)
    identity.set_password(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(record, stream)
    os.replace(temporary, path)
    print("Password saved. All devices are signed out; sign in again with the new password.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
