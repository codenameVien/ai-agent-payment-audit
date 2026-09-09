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
    aegis_model_catalog_path: str | None = None
    aegis_max_output_tokens: int = 1024
    aegis_system_prompt: str = ""
    # AEGIS is prepared, not deployed. The zero address means "no deployment yet" and is
    # recorded as such in evidence; it is never presented as a live token address.
    aegis_token_address: str = "0x0000000000000000000000000000000000000000"
    aegis_token_status: str = "prepared"
