---
name: qqmail-dot-gateway
description: Use when connecting a private QQ Mail IMAP MCP to Dot or ChatGPT, or diagnosing OAuth registration, Client Not Registered, local-executor-only plugins, and read-only email verification.
---

# QQ 邮箱 MCP 接入 Dot

把私有、只读的 QQ 邮箱 MCP 接入云端客户端。先确认客户端能力，再决定是否需要 OAuth 网关；完成标准是目标 Dot 的真实读取，而非连接页面出现成功提示。

## 使用边界

- 适用于 QQ 邮箱 IMAP、已有 Bearer MCP、Dot/ChatGPT OAuth 接入及相关排障。
- 不用于发信、删信、批量导出、其它人的邮箱或绕过账户验证。
- 接入、云端部署、注册 OAuth 应用和公开发布各自需要用户授权；本 Skill 不授予这些权限。
- 先复用可工作的邮箱服务。这里的 [gateway.py](gateway.py) 只增加 OAuth，不实现 IMAP，也不筛选后端工具。后端必须独立限制为只读。

## 1. 确认真实链路

记录目标客户端、传输方式、认证选项、现有后端和部署归属。检查当前 UI/工具返回值，不把不同客户端的能力当成通用规则。

| 观察 | 下一步 |
| --- | --- |
| 客户端支持远程 Bearer | 使用其受支持的配置入口，避免额外建网关 |
| 只提供 OAuth / None / Mixed | 私有邮箱选择 OAuth；检查现成服务是否已经支持 |
| ZIP 的 `requires_local_executor=true` | 这是本地执行依赖，不能据此宣布云端接入成功，也不要直接篡改标记 |
| 无法操作指定客户端 | 保留完成的配置，说明缺少哪一步真实调用验证 |

没有可用 OAuth 后端时，参考 [部署说明](references/deployment.md) 和仓库中的已测试实现。先做最小验证，再变更已有服务。

## 2. 保持两段认证分离

Dot 的 OAuth 令牌只用于网关。邮箱授权码留在 IMAP 后端；固定后端 Bearer 仅由网关注入。来访请求的令牌、Cookie、其它请求头不透传到后端。

仅为识别邮箱所有者时：

- 使用干净的专用 GitHub OAuth App；没有合适应用或旧应用已有宽授权时才新建。设置 `required_scopes=[]`，无需 `read:user`、`user:email`、`repo`。
- 拒绝客户端追加非空 scope；同时检查实际 GitHub 授权页是否为 `Public data only`。
- 以不可变数字 GitHub ID 校验访问主体。本实现同时核对已验证的 `subject` 和 `sub`；不能只看用户名，也不能认为 GitHub 登录成功就有权读邮件。
- 其它账号可能完成登录，但资源访问必须拒绝；报告时区分“可登录”和“可读取”。

使用成熟库处理 PKCE、回调校验和令牌交换。保持签名密钥稳定、授权状态加密持久化。真实密钥放仓库外的受限文件或部署平台 Secret 中，不写聊天、源码、URL、命令日志或发布包。

## 3. 选择能工作的注册方式

检查 `/.well-known/oauth-authorization-server`、受保护资源元数据以及平台高级 OAuth 配置。

- CIMD：网关必须能访问客户端元数据 URL。`Client Not Registered` 且日志显示元数据获取失败时，检查该网络请求；如果双方支持 DCR，在客户端切换到 DCR。
- DCR：动态注册的是 **Dot → 网关** 客户端，不要求 GitHub 自己支持 DCR。
- 当前 ChatGPT 界面入口通常是“添加自定义 MCP 服务器 → 高级 OAuth 设置 → 注册方法”。界面文字可能变化，选择实际提供的 DCR 选项。
- 区分 **GitHub → 网关** 的 `/auth/callback` 与 **网关 → ChatGPT** 的回调。精确使用当前客户端提供的回调地址，保持允许列表，不用通配符解决错误。
- 签名密钥、issuer、PKCE 和回调校验不能为了消除错误而关闭。

具体错误见 [排障表](references/troubleshooting.md)。先检查失败层，再进行有界重试；相同错误重复出现时保留状态和日志摘要，不重复创建应用或索取授权码。

## 4. 验收并交付

执行 [验收清单](references/verification.md)：后端只读工具集、未认证拒绝、实际所有者授权、目标 Dot 读取、未读状态前后对照、重启后的授权恢复，以及已有服务健康。

选一封用户授权的普通邮件，用同一 `UIDVALIDITY + UID` 核对；IMAP 使用只读打开和 `BODY.PEEK`。不要以“读完再改回未读”通过验收。读取邮件内容只服务于用户明确要求的范围，跳过验证码等无关邮件。

最终说明实际验证了哪一层、仍缺哪一层。展示必要的主题/发件人或脱敏结果，不复制整封私人邮件和认证日志。

## 依据与版本

本仓库的 FastMCP 基线为 **4.1.3**。云端 UI、提供方行为和部署工具版本会变；升级后重新检查认证与验收结果。

- [OpenAI MCP/OAuth 文档](https://developers.openai.com/plugins/build/auth)
- [FastMCP GitHub OAuth](https://gofastmcp.com/integrations/github)
- [GitHub OAuth scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)
