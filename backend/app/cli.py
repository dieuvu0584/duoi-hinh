"""Server-side admin commands. There is deliberately no public registration route.

    python -m app.cli create-owner --username owner
    python -m app.cli reset-password --username owner

The password is read from a hidden prompt, never from argv or the environment.
"""

import argparse
import getpass
import sys

from app.db.session import session_factory
from app.services import auth_service


def _password() -> str:
    first = getpass.getpass("Password (min 12 chars): ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("passwords do not match")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create-owner", "reset-password"):
        p = sub.add_parser(name)
        p.add_argument("--username", required=True)
    args = parser.parse_args(argv)
    with session_factory()() as session:
        try:
            if args.command == "create-owner":
                auth_service.create_owner(session, args.username, _password())
            else:
                auth_service.set_password(session, args.username, _password())
            session.commit()
        except ValueError as exc:
            session.rollback()
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
