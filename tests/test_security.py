from app.core.security import create_access_token, decode_access_token, hash_password, verify_password

def test_password_hashing():
    password = "correct horse battery staple"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("wrong", hashed)

def test_jwt_round_trip():
    token = create_access_token("42")
    assert decode_access_token(token) == "42"
