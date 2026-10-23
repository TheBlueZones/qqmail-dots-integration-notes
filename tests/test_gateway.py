import importlib
import json
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.oauth_proxy.models import ClientCode
from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl


# Load the application only after tests can report its missing entry point.
def module():
    assert importlib.util.find_spec("gateway") is not None, "Gateway entry point is missing"
    return importlib.import_module("gateway")


@pytest.fixture
# Provide synthetic configuration without reading personal credentials.
def configured(monkeypatch, tmp_path):
    values = {
        "OWNER_GITHUB_ID": "123456",
        "GITHUB_CLIENT_ID": "synthetic-client",
        "GITHUB_CLIENT_SECRET": "synthetic-client-secret",
        "PUBLIC_BASE_URL": "https://gateway.example.test",
        "CHATGPT_REDIRECT_URI": "https://chatgpt.com/connector_platform_oauth_redirect",
        "JWT_SIGNING_KEY": "synthetic-stable-key-abcdefghijklmnopqrstuvwxyz",
        "FASTMCP_HOME": str(tmp_path / "oauth"),
        "MAIL_BACKEND_URL": "https://mail.example.test/mcp",
        "MAIL_BACKEND_TOKEN": "synthetic-backend-credential",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MAIL_BACKEND_HOST", raising=False)
    return values


@pytest.mark.parametrize("key", [
    "OWNER_GITHUB_ID", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET",
    "PUBLIC_BASE_URL", "CHATGPT_REDIRECT_URI", "JWT_SIGNING_KEY",
    "FASTMCP_HOME", "MAIL_BACKEND_URL", "MAIL_BACKEND_TOKEN",
])
# Reject incomplete configuration instead of starting with implicit credentials.
def test_missing_configuration_fails_closed(configured, monkeypatch, key):
    monkeypatch.delenv(key)
    with pytest.raises(ValueError, match=key):
        module().build_gateway()


@pytest.mark.parametrize("owner", ["", "0", "username", "123456 ", "１２３"])
# Reject identifiers that cannot represent the exact numeric GitHub account.
def test_invalid_owner_is_rejected(configured, monkeypatch, owner):
    monkeypatch.setenv("OWNER_GITHUB_ID", owner)
    with pytest.raises(ValueError, match="OWNER_GITHUB_ID"):
        module().build_gateway()


@pytest.mark.parametrize("key,value", [
    ("PUBLIC_BASE_URL", "http://gateway.example.test"),
    ("PUBLIC_BASE_URL", "https://user:password@gateway.example.test"),
    ("PUBLIC_BASE_URL", "https://gateway.example.test/prefix?bad=1"),
    ("CHATGPT_REDIRECT_URI", "https://attacker.example.test/callback"),
    ("MAIL_BACKEND_URL", "https://user:password@mail.example.test/mcp"),
    ("MAIL_BACKEND_URL", "file:///tmp/mail"),
    ("MAIL_BACKEND_HOST", "mail.example.test\r\nAuthorization: attacker"),
    ("JWT_SIGNING_KEY", "short-key"),
    ("FASTMCP_HOME", "relative/state"),
])
# Reject unsafe URLs, headers, storage paths, and weak persistent keys.
def test_invalid_configuration_is_rejected(configured, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        module().build_gateway()


@pytest.mark.asyncio
@pytest.mark.parametrize("scopes", [["repo"], ["user:email"], ["user"], ["offline_access"]])
# Reject requests that attempt to expand the identity-only GitHub grant.
async def test_scope_escalation_is_rejected(configured, scopes):
    auth = module().build_gateway().auth
    client = OAuthClientInformationFull(client_id="client", redirect_uris=[configured["CHATGPT_REDIRECT_URI"]])
    params = AuthorizationParams(
        state="state", scopes=scopes, code_challenge="a" * 43,
        redirect_uri=AnyUrl(configured["CHATGPT_REDIRECT_URI"]),
        redirect_uri_provided_explicitly=True,
    )
    with pytest.raises(AuthorizeError) as caught:
        await auth.authorize(client, params)
    assert caught.value.error == "invalid_scope"


@pytest.mark.asyncio
# Keep the upstream authorization URL free of additional GitHub scopes.
async def test_authorization_requests_only_public_identity(configured):
    auth = module().build_gateway().auth
    client = OAuthClientInformationFull(client_id="client", redirect_uris=[configured["CHATGPT_REDIRECT_URI"]])
    params = AuthorizationParams(
        state="state", scopes=[], code_challenge="a" * 43,
        redirect_uri=AnyUrl(configured["CHATGPT_REDIRECT_URI"]),
        redirect_uri_provided_explicitly=True,
    )
    consent_url = await auth.authorize(client, params)
    transaction_id = parse_qs(urlsplit(consent_url).query)["txn_id"][0]
    transaction = await auth._transaction_store.get(key=transaction_id)
    upstream_url = auth._build_upstream_authorize_url(transaction_id, transaction.model_dump())
    query = parse_qs(urlsplit(upstream_url).query)
    assert "scope" not in query
    assert query["redirect_uri"] == ["https://gateway.example.test/auth/callback"]
    assert query["code_challenge_method"] == ["S256"]


@pytest.mark.asyncio
@pytest.mark.parametrize("subject,claim,allowed", [
    ("123456", "123456", True), ("987654", "987654", False),
    (None, "123456", False), ("123456", None, False),
    ("123456", "987654", False), ("987654", "123456", False),
])
# Deny all verified identities except the configured subject and matching claim.
async def test_owner_identity_must_match_both_fields(configured, monkeypatch, subject, claim, allowed):
    auth = module().build_gateway().auth
    verified = AccessToken(
        token="synthetic-token", client_id="client", scopes=[],
        subject=subject, claims={} if claim is None else {"sub": claim},
    )

    # Isolate the external identity verification result for malformed-claim cases.
    async def verified_identity(self, token):
        return verified

    monkeypatch.setattr(OAuthProxy, "load_access_token", verified_identity)
    actual = await auth.load_access_token("synthetic-token")
    assert (actual is not None) is allowed


@pytest.mark.asyncio
# Reject unrecognized credentials through the real JWT validator.
async def test_invalid_access_token_is_rejected(configured):
    assert await module().build_gateway().auth.load_access_token("invalid") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("github_id,allowed", [(123456, True), (987654, False)])
# Preserve encrypted OAuth state and tokens across application reconstruction.
async def test_oauth_token_survives_reconstruction(configured, github_id, allowed):
    gateway = module().build_gateway()
    auth = gateway.auth
    gateway.http_app()

    # Return synthetic GitHub identity through the real provider HTTP boundary.
    def github_response(request):
        assert request.headers["authorization"] == "Bearer synthetic-github-access"
        if request.url.path == "/user":
            return httpx2.Response(200, json={"id": github_id, "login": "synthetic-user"})
        if request.url.path == "/user/repos":
            return httpx2.Response(200, json=[], headers={"X-OAuth-Scopes": ""})
        raise AssertionError("Unexpected upstream request")

    auth._token_validator._http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(github_response))
    code = secrets.token_urlsafe(20)
    now = time.time()
    await auth._code_store.put(key=code, value=ClientCode(
        code=code, client_id="synthetic-client", redirect_uri=configured["CHATGPT_REDIRECT_URI"],
        code_challenge="a" * 43, code_challenge_method="S256", scopes=[],
        idp_tokens={"access_token": "synthetic-github-access", "token_type": "Bearer", "scope": ""},
        expires_at=now + 300, created_at=now,
    ))
    client = OAuthClientInformationFull(client_id="synthetic-client", redirect_uris=[configured["CHATGPT_REDIRECT_URI"]])
    authorization_code = await auth.load_authorization_code(client, code)
    token = await auth.exchange_authorization_code(client, authorization_code)
    recreated = module().build_gateway()
    app = recreated.http_app()
    recreated.auth._token_validator._http_client = auth._token_validator._http_client
    loaded = await recreated.auth.load_access_token(token.access_token)
    assert (loaded is not None) is allowed
    if allowed:
        assert loaded.subject == "123456"
    else:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=configured["PUBLIC_BASE_URL"]) as client:
            response = await client.post("/mcp", headers={"Authorization": "Bearer " + token.access_token}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert response.status_code == 401
    files = list(Path(configured["FASTMCP_HOME"]).rglob("*"))
    assert any(path.is_file() for path in files)
    assert all(b"synthetic-github-access" not in path.read_bytes() for path in files if path.is_file())
    await auth._token_validator._http_client.aclose()


@pytest.mark.asyncio
# Rotate expiring upstream credentials while preserving the owner's authorization.
async def test_github_refresh_token_rotation(configured, monkeypatch):
    gateway = module().build_gateway()
    auth = gateway.auth
    gateway.http_app()
    real_client = httpx2.AsyncClient

    # Provide the GitHub token and identity endpoints without external requests.
    def github_response(request):
        if request.url.path == "/login/oauth/access_token":
            body = parse_qs(request.content.decode())
            assert body["grant_type"] == ["refresh_token"]
            assert body["refresh_token"] == ["synthetic-first-refresh"]
            return httpx2.Response(200, json={
                "access_token": "synthetic-next-access", "refresh_token": "synthetic-next-refresh",
                "expires_in": 3600, "refresh_expires_in": 7200, "scope": "", "token_type": "Bearer",
            })
        if request.url.path == "/user":
            assert request.headers["authorization"] == "Bearer synthetic-next-access"
            return httpx2.Response(200, json={"id": 123456, "login": "synthetic-user"})
        if request.url.path == "/user/repos":
            return httpx2.Response(200, json=[], headers={"X-OAuth-Scopes": ""})
        raise AssertionError("Unexpected GitHub request")

    # Retain the real HTTP request encoding and replace only network delivery.
    def github_client(*args, **kwargs):
        return real_client(*args, transport=httpx2.MockTransport(github_response), **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", github_client)
    now = time.time()
    code = secrets.token_urlsafe(20)
    await auth._code_store.put(key=code, value=ClientCode(
        code=code, client_id="synthetic-client", redirect_uri=configured["CHATGPT_REDIRECT_URI"],
        code_challenge="a" * 43, code_challenge_method="S256", scopes=[],
        idp_tokens={"access_token": "synthetic-first-access", "refresh_token": "synthetic-first-refresh",
                    "expires_in": 3600, "refresh_expires_in": 7200, "token_type": "Bearer", "scope": ""},
        expires_at=now + 300, created_at=now,
    ))
    client = OAuthClientInformationFull(client_id="synthetic-client", redirect_uris=[configured["CHATGPT_REDIRECT_URI"]])
    authorization_code = await auth.load_authorization_code(client, code)
    first = await auth.exchange_authorization_code(client, authorization_code)
    refresh = await auth.load_refresh_token(client, first.refresh_token)
    rotated = await auth.exchange_refresh_token(client, refresh, [])
    assert rotated.refresh_token != first.refresh_token
    assert await auth.load_refresh_token(client, first.refresh_token) is None
    assert (await auth.load_access_token(rotated.access_token)).subject == "123456"


@pytest.mark.asyncio
# Send only the configured backend credential even when a caller supplies headers.
async def test_backend_credentials_and_headers_are_isolated(configured, monkeypatch):
    from fastmcp.client.transports import http as transport_module
    monkeypatch.setattr(transport_module, "_get_forwardable_http_headers", lambda: {
        "authorization": "Bearer attacker-token", "x-private-caller": "secret",
    })
    requests = []

    # Emulate the remote MCP protocol while recording the actual outgoing HTTP headers.
    def backend_response(request):
        requests.append(request)
        if request.method in ("GET", "DELETE"):
            return httpx2.Response(405)
        message = json.loads(request.content)
        if "id" not in message:
            return httpx2.Response(202)
        result = {
            "initialize": {
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "synthetic-mail", "version": "1"},
            },
            "tools/list": {"tools": []},
        }[message["method"]]
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    # Replace networking only; keep the production transport's header and auth handling.
    def http_client_factory(**kwargs):
        return httpx2.AsyncClient(transport=httpx2.MockTransport(backend_response), **kwargs)

    monkeypatch.setattr(transport_module, "create_mcp_http_client", http_client_factory)
    backend = module().build_gateway().client_factory()
    async with backend:
        await backend.list_tools()
    assert requests
    assert all(request.headers.get("authorization") == "Bearer synthetic-backend-credential" for request in requests)
    assert all("x-private-caller" not in request.headers for request in requests)
    assert all(request.headers["host"] == "mail.example.test" for request in requests)


@pytest.mark.asyncio
# Publish health and OAuth discovery without exposing application secrets.
async def test_health_and_discovery_are_safe(configured):
    app = module().build_gateway().http_app()
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="https://gateway.example.test") as client:
        health = await client.get("/healthz")
        metadata = await client.get("/.well-known/oauth-authorization-server")
        denied = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert metadata.status_code == 200
    assert metadata.json()["authorization_response_iss_parameter_supported"] is True
    assert "S256" in metadata.json()["code_challenge_methods_supported"]
    assert denied.status_code == 401
    assert "resource_metadata=" in denied.headers["www-authenticate"]
    assert all(value not in health.text + metadata.text for value in [configured["MAIL_BACKEND_TOKEN"], configured["GITHUB_CLIENT_SECRET"], configured["JWT_SIGNING_KEY"]])
