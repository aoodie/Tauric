import argparse
import getpass
import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hasher
from .database import engine
from .models import Owner, identifier
from .services import audit, create_workspace


def main():
    parser = argparse.ArgumentParser(description="Local Tauric administrator tools")
    sub = parser.add_subparsers(dest="command", required=True)
    owner_command = sub.add_parser("create-owner")
    owner_command.add_argument("--username", required=True)
    schema_command = sub.add_parser("export-openapi")
    schema_command.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "export-openapi":
        from .app import app

        Path(args.output).write_text(json.dumps(app.openapi(), indent=2) + "\n")
        return
    username = args.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,79}", username):
        parser.error("Use a username of 3–80 letters, numbers, dots, underscores or hyphens.")
    password = getpass.getpass("New owner password: ")
    if len(password) < 12 or len(password) > 1024:
        parser.error("Use a password of 12–1024 characters.")
    if password != getpass.getpass("Confirm password: "):
        parser.error("Passwords did not match.")
    with Session(engine()) as db:
        if db.scalar(select(Owner.id)):
            parser.error("The owner already exists. This command never overwrites an owner.")
        owner = Owner(id=identifier(), username=username, password_hash=hasher.hash(password))
        db.add(owner)
        db.flush()
        audit(
            db, owner, "owner.created", "owner", owner.id, "Local administrator created owner", {}
        )
        create_workspace(db, owner, "Trading")
    print("Owner created. Sign in to the workstation.")


if __name__ == "__main__":
    main()
