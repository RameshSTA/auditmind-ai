"""Shared pytest fixtures.

The RSA keypair fixtures let auth tests sign and validate real JWTs locally, without any network
call to a live Entra ID tenant — ``decode_and_validate_token`` and the FastAPI auth dependency are
exercised against genuine, cryptographically valid (and deliberately invalid) tokens.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

TEST_AUDIENCE = "test-api-client"
TEST_ISSUER = "https://login.microsoftonline.com/test-tenant/v2.0"
TEST_KID = "test-key-1"


@dataclass(frozen=True)
class KeyMaterial:
    private_pem: str
    jwk_dict: dict[str, Any]


@pytest.fixture(scope="session")
def rsa_keypair() -> KeyMaterial:
    """A locally-generated RSA keypair, standing in for an Entra ID signing key."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    jwk_dict = jwk.construct(public_pem, algorithm="RS256").to_dict()
    jwk_dict["kid"] = TEST_KID
    jwk_dict["alg"] = "RS256"

    return KeyMaterial(private_pem=private_pem, jwk_dict=jwk_dict)


def make_token(
    rsa_keypair: KeyMaterial,
    *,
    subject: str = "user-1",
    roles: list[str] | None = None,
    audience: str = TEST_AUDIENCE,
    issuer: str = TEST_ISSUER,
    expires_in_seconds: int = 3600,
    kid: str = TEST_KID,
) -> str:
    """Signs a test access token with the fixture's private key."""
    now = int(time.time())
    claims = {
        "sub": subject,
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": now + expires_in_seconds,
        "roles": roles or ["Auditor"],
        "tid": "test-tenant",
    }
    return jwt.encode(claims, rsa_keypair.private_pem, algorithm="RS256", headers={"kid": kid})
