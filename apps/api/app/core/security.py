"""
Verifies Clerk session JWTs on incoming requests.

How it works:
1. The frontend (Clerk's Next.js SDK) attaches a short-lived session JWT to
   every API request as `Authorization: Bearer <token>`.
2. Clerk signs these tokens with a private key; the matching public keys are
   published at a JWKS (JSON Web Key Set) URL specific to your Clerk
   instance.
3. This module fetches that JWKS once, caches it in memory, and uses it to
   verify the signature + standard claims (issuer, expiry) of each token.

This is a from-scratch verifier (PyJWT + httpx) rather than the Clerk Python
SDK, so it has no dependency on a specific SDK version's API surface and is
easy to audit line-by-line — appropriate for something that gates every
authenticated request in the system.
"""

from functools import lru_cache

import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from app.core.config import get_settings

settings = get_settings()


class ClerkConfigError(RuntimeError):
    """Raised when Clerk settings are missing — fails loudly instead of
    silently accepting unverifiable tokens."""


@lru_cache
def _get_jwks_client() -> PyJWKClient:
    if not settings.clerk_jwks_url:
        raise ClerkConfigError(
            "CLERK_JWKS_URL is not set. Add it to your .env — you can find "
            "it under Clerk Dashboard -> API Keys -> Advanced -> JWKS URL."
        )
    # PyJWKClient caches keys internally and refetches on unrecognized kid,
    # so this client can be created once and reused for the process lifetime.
    return PyJWKClient(settings.clerk_jwks_url)


def verify_session_token(token: str) -> dict:
    """
    Verifies a Clerk session JWT and returns its decoded claims.

    Raises HTTPException(401) for any invalid, expired, or unverifiable
    token — callers should not need to catch anything else.
    """
    if not settings.clerk_issuer:
        raise ClerkConfigError(
            "CLERK_ISSUER is not set. Add it to your .env — it's your Clerk "
            "instance's frontend API URL, e.g. https://xxx.clerk.accounts.dev"
        )

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except ClerkConfigError:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token expired",
        )
    except jwt.PyJWKClientError as exc:
        # Covers JWKS fetch failures and unknown key ids
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Could not verify token (JWKS lookup failed): {exc}",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid session token: {exc}",
        )

    return claims
