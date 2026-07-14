#!/usr/local/bin/python3
# Docker initializes a fresh named volume as root-owned, regardless of the image's USER — so
# /data/blobs (mounted from the `blob_storage` volume, see docker-compose.yml) is root-owned on
# first boot and the `auditmind` uid this image otherwise runs as can't create subdirectories in
# it. This entrypoint runs as root (the Dockerfile no longer sets USER), fixes ownership of the
# volume every time the container starts — cheap and idempotent when ownership is already
# correct — then drops to the `auditmind` user for the real command via exec, so it still ends up
# as PID 1 and gets signals directly.
import os
import pwd
import subprocess
import sys

APP_USER = "auditmind"


def main() -> None:
    if os.geteuid() == 0:
        user = pwd.getpwnam(APP_USER)
        blob_root = os.environ.get("AUDITMIND_BLOB_STORAGE_ROOT", "/data/blobs")
        if os.path.isdir(blob_root):
            subprocess.run(
                ["chown", "-R", f"{user.pw_uid}:{user.pw_gid}", blob_root], check=True
            )
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
        # os.setuid/setgid change the process's actual privilege bits but never touch the
        # environment — HOME stays "/root" unless set here, which is exactly what broke
        # asyncpg's readiness check the first time this ran against a real database: libpq
        # defaults to looking for TLS client material under "$HOME/.postgresql/", so a uid-1000
        # process with HOME still "/root" got a genuine `PermissionError` trying to read a
        # directory it no longer has any rights to, not a missing-file case.
        os.environ["HOME"] = user.pw_dir
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
