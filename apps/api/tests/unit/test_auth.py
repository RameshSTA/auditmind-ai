"""Unit tests for JWT validation.

Tokens are signed and verified against a locally-generated RSA keypair (see
``tests/conftest.py``) — no network call to a live Entra ID tenant is made or needed, which is
exactly what makes ``decode_and_validate_token`` a pure, fast, deterministic unit test.
"""

from __future__ import annotations

import time

import pytest
from jose import jwt as jose_jwt

from auditmind_api.shared.auth import decode_and_validate_token
from auditmind_api.shared.errors import AuthenticationError
from tests.conftest import TEST_AUDIENCE, TEST_ISSUER, KeyMaterial, make_token


def test_valid_token_decodes_successfully(rsa_keypair: KeyMaterial) -> None:
    token = make_token(rsa_keypair, subject="user-42", roles=["Auditor", "FraudAnalyst"])

    claims = decode_and_validate_token(
        token,
        rsa_keypair.jwk_dict,
        audience=TEST_AUDIENCE,
        issuer=TEST_ISSUER,
        leeway_seconds=30,
    )

    assert claims["sub"] == "user-42"
    assert claims["roles"] == ["Auditor", "FraudAnalyst"]


def test_expired_token_is_rejected(rsa_keypair: KeyMaterial) -> None:
    token = make_token(rsa_keypair, expires_in_seconds=-60)  # already expired

    with pytest.raises(AuthenticationError, match="expired"):
        decode_and_validate_token(
            token,
            rsa_keypair.jwk_dict,
            audience=TEST_AUDIENCE,
            issuer=TEST_ISSUER,
            leeway_seconds=0,
        )


def test_wrong_audience_is_rejected(rsa_keypair: KeyMaterial) -> None:
    token = make_token(rsa_keypair, audience="some-other-client")

    with pytest.raises(AuthenticationError):
        decode_and_validate_token(
            token,
            rsa_keypair.jwk_dict,
            audience=TEST_AUDIENCE,
            issuer=TEST_ISSUER,
            leeway_seconds=30,
        )


def test_wrong_issuer_is_rejected(rsa_keypair: KeyMaterial) -> None:
    token = make_token(rsa_keypair, issuer="https://not-the-real-issuer.example")

    with pytest.raises(AuthenticationError):
        decode_and_validate_token(
            token,
            rsa_keypair.jwk_dict,
            audience=TEST_AUDIENCE,
            issuer=TEST_ISSUER,
            leeway_seconds=30,
        )


def test_tampered_signature_is_rejected(rsa_keypair: KeyMaterial) -> None:
    token = make_token(rsa_keypair)
    # Flip a character in the signature segment to simulate a tampered/forged token.
    header_b64, payload_b64, signature_b64 = token.split(".")
    tampered_signature = ("A" if signature_b64[0] != "A" else "B") + signature_b64[1:]
    tampered_token = f"{header_b64}.{payload_b64}.{tampered_signature}"

    with pytest.raises(AuthenticationError):
        decode_and_validate_token(
            tampered_token,
            rsa_keypair.jwk_dict,
            audience=TEST_AUDIENCE,
            issuer=TEST_ISSUER,
            leeway_seconds=30,
        )


def test_signature_from_a_different_key_is_rejected(rsa_keypair: KeyMaterial) -> None:
    """A token signed by a completely different (attacker-controlled) key must never validate
    against this service's trusted JWKS, even if every claim inside it looks legitimate."""
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_module

    forged_private_key = rsa_module.generate_private_key(public_exponent=65537, key_size=2048)
    from cryptography.hazmat.primitives import serialization

    forged_pem = forged_private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    now = int(time.time())
    forged_token = jose_jwt.encode(
        {
            "sub": "attacker",
            "aud": TEST_AUDIENCE,
            "iss": TEST_ISSUER,
            "iat": now,
            "exp": now + 3600,
            "roles": ["Admin"],
        },
        forged_pem,
        algorithm="RS256",
        headers={"kid": "test-key-1"},
    )

    with pytest.raises(AuthenticationError):
        decode_and_validate_token(
            forged_token,
            rsa_keypair.jwk_dict,  # the service's real, legitimate public key
            audience=TEST_AUDIENCE,
            issuer=TEST_ISSUER,
            leeway_seconds=30,
        )


def test_leeway_permits_a_recently_expired_token_within_tolerance(rsa_keypair: KeyMaterial) -> None:
    token = make_token(rsa_keypair, expires_in_seconds=-10)  # expired 10s ago

    # 30s leeway should tolerate a 10s clock-skew-driven expiry.
    claims = decode_and_validate_token(
        token,
        rsa_keypair.jwk_dict,
        audience=TEST_AUDIENCE,
        issuer=TEST_ISSUER,
        leeway_seconds=30,
    )

    assert claims["sub"] == "user-1"
