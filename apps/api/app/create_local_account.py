"""Explicit local account provisioning; never changes an existing user's password."""

import argparse
import getpass
import re

from argon2 import PasswordHasher
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import User
from app.db.schema_guard import ensure_schema_current
from app.db.session import SessionLocal
from app.services.v2_runtime import create_household


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phone")
    parser.add_argument("--name", default="本地联调用户")
    args = parser.parse_args()
    if get_settings().app_env != "development":
        raise SystemExit("Only development provisioning is supported")
    if not re.fullmatch(r"1[3-9][0-9]{9}", args.phone):
        raise SystemExit("An 11-digit mainland China mobile number is required")
    password = getpass.getpass("Password (6+ characters): ")
    if len(password) < 6:
        raise SystemExit("Password must contain at least 6 characters")
    ensure_schema_current()
    with SessionLocal() as session, session.begin():
        if session.scalar(select(User).where(User.phone == args.phone)):
            raise SystemExit("Account exists; no changes made")
        user = User(
            phone=args.phone, display_name=args.name, password_hash=PasswordHasher().hash(password)
        )
        session.add(user)
        session.flush()
        user_id = user.id
    household_id = create_household(user_id, "本地联调家庭")
    print(f"Created user {user_id}, household {household_id}")


if __name__ == "__main__":
    main()
