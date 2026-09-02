from __future__ import annotations

from collections.abc import Iterable

from buyer_audit_api.core.errors import DomainNotRegisteredError
from buyer_audit_api.core.ports import DomainModule


class DomainRegistry:
    def __init__(self, modules: Iterable[DomainModule]) -> None:
        self._modules = {module.domain_id: module for module in modules}

    def require(self, domain_id: str) -> DomainModule:
        try:
            return self._modules[domain_id]
        except KeyError as exc:
            raise DomainNotRegisteredError(domain_id) from exc

    @property
    def domain_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._modules))
