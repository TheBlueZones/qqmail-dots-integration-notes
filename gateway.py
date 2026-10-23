import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastmcp import settings
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient
from mcp.server.auth.provider import AuthorizeError
from starlette.responses import JSONResponse


class PrivateGitHubProvider(GitHubProvider):
    # Bind access to one immutable numeric GitHub account identifier.
    def __init__(self, *, owner_id: str, **kwargs):
        if re.fullmatch(r"[1-9][0-9]*", owner_id) is None:
            raise ValueError("OWNER_GITHUB_ID must be a positive numeric GitHub ID")
        self.owner_id = owner_id
        super().__init__(**kwargs)

    # Permit public identity authentication without additional GitHub permissions.
    async def authorize(self, client, params):
        if params.scopes:
            raise AuthorizeError(
                error="invalid_scope",
                error_description="Additional GitHub permissions are not allowed",
            )
        return await super().authorize(client, params)

    # Reject tokens whose verified GitHub identity does not match the owner.
    async def load_access_token(self, token):
        verified = await super().load_access_token(token)
        if (
            verified is None
            or verified.subject != self.owner_id
            or verified.claims.get("sub") != self.owner_id
        ):
            return None
        return verified


# Validate deployment configuration and construct the private mail gateway.
def build_gateway():
    required = (
        "OWNER_GITHUB_ID", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET",
        "PUBLIC_BASE_URL", "CHATGPT_REDIRECT_URI", "JWT_SIGNING_KEY",
        "FASTMCP_HOME", "MAIL_BACKEND_URL", "MAIL_BACKEND_TOKEN",
    )
    config = {key: os.environ.get(key, "") for key in required}
    for key, value in config.items():
        if not value or not value.strip():
            raise ValueError(f"{key} is required")

    public_url = urlsplit(config["PUBLIC_BASE_URL"])
    if (
        public_url.scheme != "https" or not public_url.hostname
        or public_url.username is not None or public_url.password is not None
        or public_url.path not in ("", "/") or public_url.query or public_url.fragment
    ):
        raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin without credentials")

    callback = urlsplit(config["CHATGPT_REDIRECT_URI"])
    if (
        callback.scheme != "https" or callback.hostname != "chatgpt.com"
        or callback.netloc != "chatgpt.com" or callback.query or callback.fragment
        or re.fullmatch(r"/connector_platform_oauth_redirect|/connector/oauth/[A-Za-z0-9_-]+", callback.path) is None
    ):
        raise ValueError("CHATGPT_REDIRECT_URI must match the exact ChatGPT callback")

    backend_url = urlsplit(config["MAIL_BACKEND_URL"])
    if (
        backend_url.scheme not in ("http", "https") or not backend_url.hostname
        or backend_url.username is not None or backend_url.password is not None
        or backend_url.fragment
    ):
        raise ValueError("MAIL_BACKEND_URL must be an HTTP(S) URL without credentials")

    backend_host = os.environ.get("MAIL_BACKEND_HOST", backend_url.netloc)
    if not backend_host or re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", backend_host) is None:
        raise ValueError("MAIL_BACKEND_HOST must be a hostname with an optional port")
    if len(config["JWT_SIGNING_KEY"]) < 32:
        raise ValueError("JWT_SIGNING_KEY must contain at least 32 characters")
    if not Path(config["FASTMCP_HOME"]).is_absolute():
        raise ValueError("FASTMCP_HOME must be an absolute persistent directory")
    settings.home = Path(config["FASTMCP_HOME"])

    auth = PrivateGitHubProvider(
        owner_id=config["OWNER_GITHUB_ID"],
        client_id=config["GITHUB_CLIENT_ID"],
        client_secret=config["GITHUB_CLIENT_SECRET"],
        base_url=config["PUBLIC_BASE_URL"].rstrip("/"),
        required_scopes=[],
        allowed_client_redirect_uris=[config["CHATGPT_REDIRECT_URI"]],
        jwt_signing_key=config["JWT_SIGNING_KEY"],
        require_authorization_consent=True,
        enable_cimd=True,
    )

    # Open an isolated backend session using only the configured backend credential.
    def backend_client():
        return ProxyClient(
            StreamableHttpTransport(
                config["MAIL_BACKEND_URL"],
                auth=config["MAIL_BACKEND_TOKEN"],
                headers={"Host": backend_host},
            ),
            forward_incoming_headers=False,
            mode="legacy",
        )

    gateway = FastMCPProxy(
        name="Private Mail Gateway",
        auth=auth,
        provider_error_strategy="raise",
        client_factory=backend_client,
    )

    @gateway.custom_route("/healthz", methods=["GET"])
    # Report process readiness without querying or revealing mail credentials.
    async def health(request):
        return JSONResponse({"status": "ok"})

    return gateway


if __name__ == "__main__":
    build_gateway().run(transport="http", host="0.0.0.0", port=8000)
