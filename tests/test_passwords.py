from backend.auth.passwords import hash_password, verify_password

PASSWORD = "Rahul@Demo2026!"


def test_a_password_is_stored_as_an_argon2id_hash_never_as_itself():
    stored = hash_password(PASSWORD)
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


def test_the_right_password_matches_and_a_wrong_one_does_not():
    stored = hash_password(PASSWORD)
    assert verify_password(PASSWORD, stored)
    assert not verify_password("rahul@demo2026!", stored)
    assert not verify_password("", stored)


def test_the_same_password_gets_a_different_hash_each_time():
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_an_account_without_a_hash_never_matches_whatever_is_typed():
    assert not verify_password(PASSWORD, None)
    assert not verify_password("no account has this password", None)  # the stand-in's own password gets nobody in


def test_an_unreadable_stored_hash_never_matches():
    assert not verify_password(PASSWORD, "not-a-hash")
    assert not verify_password(PASSWORD, PASSWORD)  # a plain password left in the hash column is not accepted
