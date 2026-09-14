from __future__ import annotations

from dataclasses import replace

import pytest

from buyer_audit_api.api.app import create_app
from buyer_audit_api.composition import build_container
from buyer_audit_api.core.errors import ConfigurationError
from buyer_audit_api.settings import Settings


@pytest.mark.parametrize("owner", ["", "   ", "not-an-address", "0x1234"])
def test_app_refuses_to_start_without_a_valid_local_owner(container, owner: str) -> None:
    with pytest.raises(ConfigurationError, match="Ethereum address"):
        create_app(replace(container, local_owner_address=owner))


@pytest.mark.parametrize("owner", ["", "not-an-address"])
def test_container_refuses_to_build_without_a_valid_local_owner(owner: str) -> None:
    settings = Settings.model_construct(aegis_local_owner_address=owner)
    with pytest.raises(ConfigurationError, match="AEGIS_LOCAL_OWNER_ADDRESS"):
        build_container(settings)
