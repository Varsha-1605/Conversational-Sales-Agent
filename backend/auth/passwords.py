from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# DATA_SPEC 2.1, 2.9: only an argon2id hash of a password is ever stored, for customers and staff alike. The
# library's default costs are its current recommendation for argon2id.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128  # a limit on what one request can make the server hash

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """The argon2id hash to store for a password. Every call gives a different hash (random salt)."""
    return _hasher.hash(password)


@cache
def _hash_of_nobody() -> str:
    return _hasher.hash("no account has this password")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Whether the password matches the stored hash. A missing or unreadable hash never matches. The time taken is
    the same whether or not the account exists, so a reply's speed does not show which emails are registered."""
    try:
        matches = _hasher.verify(password_hash or _hash_of_nobody(), password)
    except (VerificationError, InvalidHashError):
        return False
    return matches and password_hash is not None
