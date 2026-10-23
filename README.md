# QQ 邮箱接入 Dots 踩坑实录

**这是一份主要给 Agent 阅读、排障和参考的踩坑经验。人类读者看本页简洁结论即可。**

- 已走通：Dots → OAuth 网关 → 只读 Mail MCP → QQ 邮箱。
- 关键坑：本地 MCP 可用不代表 Dots 云端可用；CIMD 注册失败时，应检查是否可以使用 DCR；GitHub 登录成功还需要邮箱访问者身份校验。
- 验收结果：Dots 实际读取了指定邮件，邮件仍保持未读，网关重启后授权继续可用。

记录基于一次实际接入，受当时版本、账号能力和部署环境约束。使用方式是按需查阅，复用前重新核验当前环境。

## 给 Agent 的入口

从 [AGENT-GUIDE.md](AGENT-GUIDE.md) 开始。其中提供接入背景、踩坑判断、详细资料入口和验收边界。

可直接把下面这句话交给 Agent：

> 请阅读这个仓库的 AGENT-GUIDE.md，把它作为踩坑经验参考，先核对我的当前环境，再处理 QQ 邮箱接入 Dots 的问题。

仓库保留 [网关源码](gateway.py)、[部署说明](references/deployment.md)、[排障表](references/troubleshooting.md) 和 [验收清单](references/verification.md)。源码及文档采用 [MIT](LICENSE)；可选的 [mail-mcp-suite](https://github.com/federicokalik/mail-mcp-suite) 后端是独立的 AGPL-3.0 项目，未打包或重新授权。

真实邮箱内容、账号凭据及部署配置未包含在仓库中。本项目是独立经验分享及示例实现。

项目地址：[qqmail-dots-integration-notes](https://github.com/TheBlueZones/qqmail-dots-integration-notes)。
