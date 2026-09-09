from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    mongodb_uri: str = "mongodb://localhost:27017/?directConnection=true"
    mongodb_database: str = "pbl_audit"
    siwe_domain: str = "localhost"
    siwe_uri: str = "http://localhost:3000"
    siwe_chain_id: int = 84532
    cookie_secure: bool = False
    session_secret_base64: str
    payload_master_key_base64: str
    internal_service_token: str
    admin_service_token: str
    commerce_gateway_url: str = "http://localhost:8081"
    gateway_service_token: str
    base_sepolia_rpc_url: str | None = None
    seller_routes_json: str = (
        '{"gemini":"http://localhost:8080","nemotron":"http://localhost:8082"}'
    )
    quote_verifying_contract: str = "0x0000000000000000000000000000000000000001"
    erc8004_identity_registry: str = "0x8004A818BFB912233c491871b3d84c89A494BD9e"
    gemini_model_id: str = "gemini-2.5-flash"
    gemini_model_version: str = "gemini-2.5-flash"
    nemotron_model_id: str = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    nemotron_model_version: str = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
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
    aegis_max_output_tokens: int = 1024
    aegis_system_prompt: str = ""
    # PBLC V2 is the already deployed six-decimal ERC-3009 test token. The local
    # composition still uses a Mock Facilitator, so this address is payment terms only
    # until a separately approved real settlement is performed.
    pblc_token_address: str = "0xDed7F4992D98eF31453dCebbB8c2A6b50d0284B3"
    pblc_token_status: str = "deployed-base-sepolia"
    # Single-user local demo identity. This is server configuration, not a login: the
    # browser can never choose it. Clearing it restores the historical SIWE composition
    # for reading PBLC history, and that legacy mode is manual only.
    aegis_local_owner_address: str = "0x00000000000000000000000000000000000a6e15"
    aegis_local_allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    aegis_local_allowed_hosts: str = "localhost,127.0.0.1"
    # Mock providers and a Mock Facilitator are the only execution this runtime performs.
    # The mode is recorded from here, so no client can present mock evidence as live.
    aegis_execution_mode: str = "mock"
