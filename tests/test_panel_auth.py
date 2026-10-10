"""Testy panel/auth.py: token, sesje, Host, Origin."""
from panel.auth import COOKIE_NAME, PanelAuth


def make_auth(port=8102):
    return PanelAuth(port=port)


def test_token_is_random_and_long():
    a, b = make_auth(), make_auth()
    assert len(a.token) >= 32
    assert a.token != b.token


def test_exchange_valid_token_returns_session():
    auth = make_auth()
    session = auth.exchange_token(auth.token)
    assert session
    assert session != auth.token
    assert auth.is_valid_session(session)


def test_exchange_gives_distinct_sessions():
    auth = make_auth()
    s1, s2 = auth.exchange_token(auth.token), auth.exchange_token(auth.token)
    assert s1 != s2
    assert auth.is_valid_session(s1) and auth.is_valid_session(s2)


def test_exchange_wrong_token_rejected():
    auth = make_auth()
    assert auth.exchange_token("zly-token") is None
    assert auth.exchange_token(auth.token + "x") is None
    assert auth.exchange_token(auth.token[:-1]) is None


def test_exchange_empty_or_none_rejected():
    auth = make_auth()
    assert auth.exchange_token("") is None
    assert auth.exchange_token(None) is None
    assert auth.exchange_token(123) is None


def test_token_of_other_instance_rejected():
    a, b = make_auth(), make_auth()
    assert a.exchange_token(b.token) is None


def test_session_validation():
    auth = make_auth()
    assert not auth.is_valid_session("nieznana")
    assert not auth.is_valid_session("")
    assert not auth.is_valid_session(None)
    assert not auth.is_valid_session(auth.token)  # token nie jest identyfikatorem sesji


def test_session_of_other_instance_invalid():
    a, b = make_auth(), make_auth()
    session = a.exchange_token(a.token)
    assert not b.is_valid_session(session)


def test_allowed_hosts():
    auth = make_auth()
    assert auth.is_allowed_host("127.0.0.1:8102")
    assert auth.is_allowed_host("localhost:8102")
    assert auth.is_allowed_host("LOCALHOST:8102")
    assert auth.is_allowed_host(" 127.0.0.1:8102 ")


def test_rejected_hosts():
    auth = make_auth()
    assert not auth.is_allowed_host("evil.com")
    assert not auth.is_allowed_host("evil.com:8102")
    assert not auth.is_allowed_host("127.0.0.1")
    assert not auth.is_allowed_host("127.0.0.1:8101")
    assert not auth.is_allowed_host("127.0.0.1:8102.evil.com")
    assert not auth.is_allowed_host("0.0.0.0:8102")
    assert not auth.is_allowed_host("")
    assert not auth.is_allowed_host(None)


def test_allowed_origins():
    auth = make_auth()
    assert auth.is_allowed_origin("http://127.0.0.1:8102")
    assert auth.is_allowed_origin("http://localhost:8102")
    assert auth.is_allowed_origin("HTTP://LOCALHOST:8102")


def test_missing_origin_is_allowed_at_auth_level():
    assert make_auth().is_allowed_origin(None)


def test_rejected_origins():
    auth = make_auth()
    assert not auth.is_allowed_origin("http://evil.com")
    assert not auth.is_allowed_origin("http://127.0.0.1:8101")
    assert not auth.is_allowed_origin("https://127.0.0.1:8102")
    assert not auth.is_allowed_origin("null")
    assert not auth.is_allowed_origin("")
    assert not auth.is_allowed_origin("http://127.0.0.1:8102.evil.com")


def test_custom_port():
    auth = make_auth(port=9000)
    assert auth.is_allowed_host("127.0.0.1:9000")
    assert not auth.is_allowed_host("127.0.0.1:8102")
    assert auth.is_allowed_origin("http://localhost:9000")
    assert not auth.is_allowed_origin("http://localhost:8102")


def test_cookie_name_constant():
    assert COOKIE_NAME == "vibecoder_panel"
