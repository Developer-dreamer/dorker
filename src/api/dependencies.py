"""Resolve optional services after Litestar has validated endpoint inputs.

Controllers use NamedDependency[SkipValidation[...]] only for injected structural
protocols, which are not runtime-validated objects. HTTP bodies and parameters
still go through normal validation before require_service is called.
"""

from litestar.exceptions import HTTPException


def require_service[T](service: T | None, domain: str) -> T:
    # Missing wiring must not mask invalid HTTP inputs with a premature 503 from a provider.
    if service is None:
        raise HTTPException(status_code=503, detail=f"{domain} service is not configured.")
    return service
