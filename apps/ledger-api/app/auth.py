"""Access-token verification against the auth-service's published JWKS.

The auth-service signs tokens with its private key; this service only ever sees the
public key, fetched from JWKS_URL and cached, so no request round-trips to auth.
"""

import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

ALGORITHMS = ["EdDSA"]

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    email: str


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(settings.jwks_url, cache_keys=True, lifespan=300)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


# Sync on purpose: PyJWKClient fetches over blocking HTTP, so FastAPI runs this in a threadpool.
def get_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if creds is None:
        raise _unauthorized("missing_token")
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(creds.credentials)
        claims = jwt.decode(
            creds.credentials,
            signing_key.key,
            algorithms=ALGORITHMS,
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        return Principal(
            user_id=uuid.UUID(claims["sub"]),
            org_id=uuid.UUID(claims["org_id"]),
            role=claims["role"],
            email=claims.get("email", ""),
        )
    except jwt.PyJWKClientConnectionError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth_keys_unavailable") from exc
    except (jwt.PyJWKClientError, jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise _unauthorized("invalid_token") from exc


CurrentUser = Annotated[Principal, Depends(get_principal)]


def require_roles(*roles: str):
    def check(principal: CurrentUser) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return principal

    return check
