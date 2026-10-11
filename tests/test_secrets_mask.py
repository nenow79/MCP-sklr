"""Testy maskowania sekretów (P14): rozpoznawanie plików i maskowanie wartości."""
import pytest

from server.secrets_mask import BLOCK_MASK, MASK, is_blocked_secret, is_secret_file, mask_text


@pytest.mark.parametrize("name", [
    ".env", ".ENV", "sub/.env", ".env.local", ".env.production", "pip.ini", "pip.conf", ".npmrc",
    "certs/server.pem", "id.key", "C:/x/y/.env",
])
def test_secret_files_detected(name):
    assert is_secret_file(name)


@pytest.mark.parametrize("name", [
    ".env.example", ".env.sample", ".env.template", "sub/.env.example", "env.txt", "README.md",
    "main.py", "keyboard.py", "config.json", "environment.yml",
])
def test_non_secret_files_not_detected(name):
    assert not is_secret_file(name)


def test_blocked_binary_secret_files():
    assert is_blocked_secret("cert.pfx") and is_blocked_secret("dir/a.P12")
    assert not is_blocked_secret("cert.pem") and not is_blocked_secret("x.txt")


def test_non_secret_file_is_untouched():
    text = "DB_PASSWORD=hunter2\n"
    result = mask_text("settings.py", text)
    assert result.text == text and result.count == 0 and result.masked is False


def test_env_values_masked_and_counted():
    text = ("NAME=app\nDB_PASSWORD=hunter2\nAPI_TOKEN='abc123'\n# SECRET=commented\n"
            "export AWS_SECRET_KEY=\"zzz\"\nDEBUG=true\n")
    result = mask_text(".env", text)
    assert result.count == 3 and result.masked is True
    assert "hunter2" not in result.text and "abc123" not in result.text and "zzz" not in result.text
    assert "NAME=app" in result.text and "DEBUG=true" in result.text
    assert "DB_PASSWORD=" + MASK in result.text and "export AWS_SECRET_KEY=" + MASK in result.text
    assert "# SECRET=commented" in result.text  # komentarz nie jest przypisaniem


def test_env_crlf_preserved():
    result = mask_text(".env", "A_TOKEN=x\r\nB=1\r\n")
    assert result.text == "A_TOKEN=" + MASK + "\r\nB=1\r\n" and result.count == 1


def test_env_empty_and_already_masked_values_not_counted():
    result = mask_text(".env", "TOKEN=\nPASSWORD=" + MASK + "\nSECRET=''\n")
    assert result.count == 0


def test_masking_is_idempotent():
    first = mask_text(".env", "A_PASSWORD=abc\nURL=http://u:p@h/x\n")
    second = mask_text(".env", first.text)
    assert second.text == first.text and second.count == 0


def test_url_credentials_masked_without_secret_name():
    result = mask_text(".env", "DATABASE_URL=postgres://user:pass@db:5432/app\nPLAIN=http://example.com/a\n")
    assert result.count == 1
    assert "pass@" not in result.text and "postgres://user:" + MASK + "@db:5432/app" in result.text
    assert "PLAIN=http://example.com/a" in result.text


def test_pip_ini_url_credentials_masked():
    text = "[global]\nindex-url = https://user:pw123@pypi.example.com/simple\n"
    result = mask_text("pip.ini", text)
    assert "pw123" not in result.text and result.count == 1 and "[global]" in result.text


def test_npmrc_auth_token_masked():
    text = "//registry.npmjs.org/:_authToken=npm_abcdef\nregistry=https://registry.npmjs.org/\n"
    result = mask_text(".npmrc", text)
    assert "npm_abcdef" not in result.text and result.count == 1
    assert "registry=https://registry.npmjs.org/" in result.text


def test_example_env_is_not_masked():
    text = "API_TOKEN=your-token-here\n"
    result = mask_text(".env.example", text)
    assert result.text == text and result.count == 0


def test_pem_private_key_body_replaced():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\nDEF123\n-----END RSA PRIVATE KEY-----\n"
    result = mask_text("server.pem", text)
    assert result.count == 1 and "MIIEabc" not in result.text and "DEF123" not in result.text
    assert BLOCK_MASK in result.text and "-----BEGIN RSA PRIVATE KEY-----" in result.text
    assert "-----END RSA PRIVATE KEY-----" in result.text
    again = mask_text("server.pem", result.text)
    assert again.text == result.text and again.count == 0


def test_pem_unterminated_private_key_masked():
    result = mask_text("server.pem", "-----BEGIN PRIVATE KEY-----\nSEKRETNE\nDANE")
    assert "SEKRETNE" not in result.text and "DANE" not in result.text and result.count == 1


def test_pem_certificate_is_left_alone():
    text = "-----BEGIN CERTIFICATE-----\nMIIDpublic\n-----END CERTIFICATE-----\n"
    result = mask_text("ca.pem", text)
    assert result.text == text and result.count == 0


def test_raw_key_file_without_pem_header_fully_masked():
    result = mask_text("id.key", "abcdef0123456789\n")
    assert result.text == BLOCK_MASK and result.count == 1
    assert mask_text("id.key", BLOCK_MASK).count == 0
