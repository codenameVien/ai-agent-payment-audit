from typing import Literal

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
    payment_transfer_method: Literal["permit2", "eip3009"] = "permit2"
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
