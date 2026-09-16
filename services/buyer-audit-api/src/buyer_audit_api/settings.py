from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    mongodb_uri: str = "mongodb://localhost:27017/?directConnection=true"
    mongodb_database: str = "pbl_audit"
    # Base Sepolia. The AEGIS token identity recorded with every decision carries it.
    aegis_chain_id: int = 84532
    internal_service_token: str
    admin_service_token: str
    commerce_gateway_url: str = "http://localhost:8081"
    gateway_service_token: str
    # aa-three-factor-v1. The AA key is a server-only value; absent means fixture mode
    # and the live Artificial Analysis contract stays an unfinished external gate.
    aa_api_key: str | None = None
    aa_field_paths_json: str = "{}"
    # Test-only: point the fixture capture at a different set of shipped AA pages so the
    # abnormal-evidence scenarios can be exercised against the real service. Empty means
    # the shipped pages. It has no effect once AA_API_KEY selects the live adapter.
    aa_fixture_pages_json: str = ""
    # Exact provider-model -> AA id/slug mappings. A live AA key is rejected unless this
    # catalog is explicitly configured; no fuzzy matching is permitted.
    aa_model_catalog_path: str | None = None
    # standard-answer-v1: a fixed maximum output allocation used for the one-task quote.
    aegis_max_output_tokens: int = 8_000
    aegis_system_prompt: str = ""
    # Existing tests and non-demo deployments keep deterministic keyword classification.
    # The local runner explicitly opts into the bounded loopback Qwen classifier.
    aegis_priority_classifier: str = "deterministic"
    aegis_ollama_url: str = "http://127.0.0.1:11434"
    aegis_ollama_model: str = "qwen3.5:4b"
    aegis_ollama_timeout_seconds: float = 8.0
    # Disabled by default so existing clients keep their current deterministic flow.
    aegis_observer_mode: str = "off"
    aegis_observer_timeout_seconds: float = 30.0
    aegis_checkpoint_mode: str = "off"
    # User-owned six-decimal ERC-3009 AEGIS token on Base Sepolia. Whether a request can
    # settle is controlled only by the server-side execution mode and approval gate.
    aegis_token_address: str = "0x3440294d5fdc4849461c6f383a7fcf89af0c4a4b"
    aegis_token_status: str = "deployed-base-sepolia"
    payment_token_name: str = "AEGIS"
    payment_token_symbol: str = "AEGIS"
    # Single-user local demo identity. This is server configuration, not a login: the
    # browser can never choose it. The service refuses to start when it is blank or not
    # an Ethereum address.
    aegis_local_owner_address: str = "0x00000000000000000000000000000000000a6e15"
    aegis_local_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    aegis_local_allowed_hosts: str = "localhost,127.0.0.1"
    # The server records this mode; a client can never label its own evidence as live.
    aegis_execution_mode: str = "mock"
