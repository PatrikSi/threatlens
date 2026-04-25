from app.core.security import get_password_hash, pwd_context, verify_password


def test_new_password_hashes_use_bcrypt_sha256_to_avoid_bcrypt_truncation():
    password = "x" * 72 + "first"
    same_prefix_different_suffix = "x" * 72 + "second"

    password_hash = get_password_hash(password)

    assert pwd_context.identify(password_hash) == "bcrypt_sha256"
    assert verify_password(password, password_hash) is True
    assert verify_password(same_prefix_different_suffix, password_hash) is False


def test_legacy_bcrypt_hashes_reject_overlong_passwords_before_truncation():
    legacy_hash = pwd_context.hash("x" * 72, scheme="bcrypt")

    assert verify_password("x" * 72, legacy_hash) is True
    assert verify_password("x" * 72 + "suffix", legacy_hash) is False
