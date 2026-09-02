from buyer_audit_api.api.app import create_app
from buyer_audit_api.composition import build_container
from buyer_audit_api.settings import Settings

app = create_app(build_container(Settings()))  # type: ignore[call-arg]
