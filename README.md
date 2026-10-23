# QQ Mail to Dot — Agent Skill & OAuth Gateway

把私有 QQ 邮箱 MCP 接入 Dot / ChatGPT 的可复用 **Agent Skill**，附一个经过测试的 GitHub OAuth 网关。包含部署、权限边界、CIMD/DCR 排障和实际读取验收。

| 入口 | 用途 |
| --- | --- |
| [SKILL.md](SKILL.md) | Agent 执行入口；可以单独阅读或由支持 Agent Skills 的客户端加载 |
| [部署说明](references/deployment.md) | QQ IMAP 后端、网关配置、Docker 和 Openship 注意事项 |
| [排障表](references/troubleshooting.md) | 认证、网络、回调、浏览器与部署错误 |
| [验收清单](references/verification.md) | 目标 Dot 真实读取、未读标志与重启恢复 |

在 Agent 中加载仓库根目录的 Skill 后，可使用：

> 使用 $qqmail-dot-gateway，把我现有的只读 QQ 邮箱 MCP 接入 Dot，并验证真实读取和未读状态。

加载 Skill 只提供操作指导，不会自动部署、连接邮箱或赋予账号权限。不同账号的 Dot/ChatGPT 功能入口可能不同，使用当前客户端实际支持的能力。

## Gateway

Expose an existing read-only mail MCP to ChatGPT through GitHub OAuth. Only one
configured numeric GitHub account ID can call the MCP. The existing backend keeps
its separate Bearer credential; inbound credentials and headers are not forwarded.

**Read-only behavior belongs to the backend.** This gateway proxies the backend's
entire tool set; it does not filter tools, restrict mailboxes, or make a writable
server read-only. Verify the backend's exposed tools before connecting it.
Use HTTPS for a public backend. HTTP is intended only for a protected local or
private network hop.

Use a **new dedicated GitHub OAuth App** with no requested scopes. Existing apps
may inherit previously approved GitHub permissions when scopes are omitted.
Expiring GitHub user tokens are supported, including refresh-token rotation.

## External configuration

Keep real configuration outside this repository. Supply all variables below:

| Variable | Purpose |
| --- | --- |
| `OWNER_GITHUB_ID` | Immutable positive numeric GitHub account ID |
| `GITHUB_CLIENT_ID` | Dedicated GitHub OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth App client secret |
| `PUBLIC_BASE_URL` | HTTPS origin for this gateway, without a path |
| `CHATGPT_REDIRECT_URI` | Exact callback copied from the ChatGPT connection |
| `JWT_SIGNING_KEY` | Stable random secret of at least 32 characters |
| `FASTMCP_HOME` | Absolute path on a persistent writable volume |
| `MAIL_BACKEND_URL` | Existing mail MCP HTTP(S) endpoint |
| `MAIL_BACKEND_TOKEN` | Existing backend Bearer token |
| `MAIL_BACKEND_HOST` | Optional Host header override; defaults to backend URL authority |

Set the GitHub App callback to `${PUBLIC_BASE_URL}/auth/callback`. Route the
whole gateway origin to port `8000`, including `/.well-known`, `/authorize`,
`/register`, `/token`, `/consent`, `/auth/callback`, and `/mcp`.
Keep consent enabled. The gateway rejects every nonempty requested scope and
denies a valid OAuth token unless both verified identity fields match the owner.

In ChatGPT's advanced OAuth settings, select **Dynamic Client Registration
(DCR)** when the gateway cannot retrieve ChatGPT's Client ID Metadata Document
(CIMD). This avoids the `Client Not Registered` error caused by an unreachable
CIMD URL. Keep `CHATGPT_REDIRECT_URI` exactly equal to the callback shown for the
connection; changing registration methods does not relax callback validation.

For Docker, mount a stable volume at `/data/fastmcp` writable by UID/GID `10001`.
The image sets `FASTMCP_HOME` to that path. Keep `JWT_SIGNING_KEY` stable across
restarts: changing it invalidates existing authorizations and selects a different
encrypted state directory. No database or Redis is needed for a single instance.
Never point `MAIL_BACKEND_URL` back to this gateway's public `/mcp` route.

## Run and test

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest -q
# Supply external configuration, then:
.venv/bin/python gateway.py
```

`/healthz` reports process readiness only; it does not authenticate to GitHub or
read mail. Tests use synthetic identities and isolated network responses. Their
success does not prove production OAuth, DNS, TLS, mailbox access, or memory use.
Production acceptance requires an actual owner login and read-only mail call,
rejection of another account, and a restart without losing authorization.

FastMCP is pinned to `4.1.3`; `requirements.txt` pins and hashes its dependencies.
Sources: [GitHub OAuth](https://gofastmcp.com/integrations/github),
[OAuth proxy](https://gofastmcp.com/servers/auth/oauth-proxy),
[OpenAI authentication](https://developers.openai.com/plugins/build/auth).

## License and upstream projects

Original gateway code and Skill documentation: [MIT](LICENSE).
FastMCP and the MCP SDK retain their own licenses. The optional
[mail-mcp-suite](https://github.com/federicokalik/mail-mcp-suite) backend is a
separate AGPL-3.0 project; its source and container image are not bundled or
relicensed here. This project is an independent integration, not an official
QQ, GitHub, OpenAI, or Openship connector.
