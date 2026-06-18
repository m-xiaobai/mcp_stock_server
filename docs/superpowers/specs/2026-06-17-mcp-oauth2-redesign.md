# MCP Server OAuth2 Redesign

## Summary

将当前 `mcp_stock_server` 的认证方案重设计为 **“双入口、单认证抽象、双校验后端”**：

- `streamable-http`：启用 OAuth2
- `stdio`：继续保留开发态默认 `AuthContext`
- OAuth2 模式同时兼容：
  - **JWT/JWKS 本地校验**
  - **OAuth2 Introspection 远程校验**

设计目标不是让 server 充当授权服务器，而是让它成为一个 **OAuth2 Resource Server**。它只负责：

- 接收 Bearer Token
- 校验 token
- 继续复用当前 `ToolDispatcher` / `PolicyEngine` / `required_scopes`

## Key Changes

### 1. 认证架构重构为三层

- 新增 **认证配置层**：负责读取 `config.json` 中的 OAuth2 配置，并决定启用哪种 verifier。
- 新增 **认证验证层**：定义统一 `TokenVerifier` 适配接口，屏蔽 JWT/JWKS 与 introspection 差异。
- 新增 **认证边界层**：把 OAuth2 校验限制在 HTTP 入口，不把 token 内的 scope 直接映射成业务侧工具权限。

这样设计后：

- `PolicyEngine` 不需要理解 JWT、JWKS、introspection
- `server.py` 只关心“当前请求是否已经通过 HTTP 入口认证”
- 后续新增第三种 verifier（例如网关透传 claim）不需要改业务授权模型

### 2. 入口策略明确分流

- `stdio`：
  - 不启用 OAuth2
  - 保留 `build_development_auth_context(...)`
  - 继续作为本地开发与调试入口
- `streamable-http`：
  - 如果 `auth.enabled=false`，允许匿名访问（仅用于开发或内网调试）
  - 如果 `auth.enabled=true`，必须启用 Bearer Token 校验
  - HTTP 请求认证失败时，由 FastMCP / SDK auth 中间件直接返回 `401/403`
- 不把 `stdio` 和 HTTP 统一进同一个认证策略，避免把本地开发链路变重

### 3. OAuth2 模式重设计为“资源服务器 only”

- 不实现 `auth_server_provider`
- 不提供 `/authorize`、`/token`、`/register`
- 仅使用：
  - `AuthSettings`
  - `token_verifier`
  - `resource_server_url`
  - `required_scopes`
- 启用后，让 FastMCP 自动提供：
  - Bearer Auth middleware
  - `WWW-Authenticate` challenge
  - Protected Resource Metadata 路由

### 4. 统一 verifier 抽象，兼容两种 token 校验来源

新增统一 verifier 工厂，根据配置选择：

#### A. `jwt-jwks`

适用于 OIDC / JWT access token 提供方

- 配置：
  - `issuer_url`
  - `resource_server_url`
  - `audience`
  - `jwks_uri` 或 `discovery_url`
  - `clock_skew_seconds`
  - `cache_ttl_seconds`
- 校验项：
  - 签名
  - `iss`
  - `exp` / `nbf` / `iat`
  - `aud` 或 `resource`
  - `scope` / `scp`
- 结果映射为 SDK `AccessToken`

#### B. `introspection`

适用于 opaque token 或要求中心化校验的提供方

- 配置：
  - `issuer_url`
  - `resource_server_url`
  - `introspection_endpoint`
  - `client_id`
  - `client_secret`
  - `cache_ttl_seconds`
  - `clock_skew_seconds`
- 调用 introspection endpoint
- 校验项：
  - `active=true`
  - `exp`
  - `aud` 或 `resource`
  - `scope`
- 结果映射为 SDK `AccessToken`

### 5. 配置模型重新设计

在 `config.json` 中增加完整 `mcp.auth` 段：

```json
{
  "mcp": {
    "transport": "streamable-http",
    "host": "127.0.0.1",
    "port": 8000,
    "streamable_http_path": "/mcp",
    "auth": {
      "enabled": true,
      "mode": "resource-server",
      "verification": "jwt-jwks",
      "issuer_url": "https://issuer.example.com",
      "resource_server_url": "http://127.0.0.1:8000/mcp",
      "audience": "mcp-stock-server",
      "jwks_uri": "https://issuer.example.com/.well-known/jwks.json",
      "required_scopes": ["stock:daily:read"],
      "clock_skew_seconds": 60,
      "cache_ttl_seconds": 300
    }
  }
}
```

对于 introspection 模式：

```json
{
  "mcp": {
    "auth": {
      "enabled": true,
      "mode": "resource-server",
      "verification": "introspection",
      "issuer_url": "https://issuer.example.com",
      "resource_server_url": "http://127.0.0.1:8000/mcp",
      "introspection_endpoint": "https://issuer.example.com/oauth2/introspect",
      "client_id": "mcp-stock-server",
      "client_secret": "secret",
      "required_scopes": ["stock:daily:read"],
      "clock_skew_seconds": 60,
      "cache_ttl_seconds": 60
    }
  }
}
```

配置约束：

- `mode` 第一阶段只允许 `resource-server`
- `verification` 必填，且必须为 `jwt-jwks` 或 `introspection`
- `resource_server_url` 在 HTTP + auth 模式下必填
- `required_scopes` 代表 HTTP 入口最小访问范围，不替代工具级授权模型
- JWT 模式下必须有 `audience`
- introspection 模式下必须有 `client_id/client_secret/introspection_endpoint`

### 6. HTTP 认证与工具授权分离

OAuth2 只负责保护 `streamable-http` 入口，不负责把 token 中的 `scope` / `scp` 变成内部工具权限。

HTTP 请求一旦通过 FastMCP 的 Bearer Token 校验，进入 `dispatch_tool(...)` 时仍沿用当前开发态授权路径：

- `AuthSettings.required_scopes`
  - 仅用于 HTTP 入口最小 scope 校验
- token `scope` / `scp`
  - 不映射到内部 `AuthContext.scopes`
- 工具执行阶段
  - 继续使用 `build_development_auth_context(...)`
  - 保持与原有本地开发链路一致的工具权限与 destructive approval 语义

`dispatch_tool(...)` 改成：

- `stdio` -> `build_development_auth_context(...)`
- `streamable-http + auth.enabled` -> 先走 OAuth2 入口认证，再进入 `build_development_auth_context(...)`
- `streamable-http + auth.disabled` -> 仍走开发态或最小匿名上下文，取决于运行模式；第一阶段推荐开发态仅限 localhost

### 7. 工具级授权模型不重写

保留当前工具定义里的：

- `required_scopes`
- `destructive`

保留当前：

- `PolicyEngine`
- `ApprovalChecker`

OAuth2 不直接参与工具级权限决策，也不负责 destructive approval 自动放行。这样可以保证：

- `stock:daily:write` 不自动等于 destructive 批准
- 高风险工具仍可独立治理

### 8. Manifest 与运行文档同步更新

`capability_manifest` 增加认证摘要字段：

- `transport`
- `auth_enabled`
- `auth_mode`
- `verification`
- `issuer_url`
- `resource_server_url`

不把 `client_secret`、`introspection_endpoint` 等敏感或内部细节暴露进 manifest。

同时更新设计文档，明确：

- `stdio`：开发入口，无 OAuth2
- `streamable-http`：可启用 OAuth2
- 客户端需要通过 `Authorization: Bearer ...` 访问
- HTTP 认证成功后，工具阶段仍沿用当前开发态授权上下文，不读取 token scope 做工具授权

## Implementation Changes

### Config and models

- 在 `main.py` 或独立配置模块中，把 `MCPRuntimeConfig` 扩成：
  - `MCPAuthConfig`
  - `MCPJwtJwksConfig`
  - `MCPIntrospectionConfig`
- 解析时做模式约束和字段完整性校验
- 不把 OAuth2 配置并入 MySQL 配置模型

### Auth package

- 在 `auth/` 下新增：
  - verifier 工厂
  - JWT/JWKS verifier
  - Introspection verifier
- 保持 `AuthContext` 仍为内部统一授权结构，不让业务层直接依赖 SDK 的 `AccessToken`

### Server wiring

- `create_mcp_server(...)` 新增 `auth_config`
- 仅当：
  - `transport="streamable-http"`
  - `auth.enabled=true`
  时，为 FastMCP 传：
  - `auth=AuthSettings(...)`
  - `token_verifier=...`
- 否则不传 auth 相关参数
- `run_streamable_http_server(...)` 接收 `auth_config`

### Audit behavior

- 审计中新增认证主体字段来源说明：
  - 认证用户/客户端 ID
  - scope 列表
- 严格禁止把 Bearer Token、client_secret、introspection 原始响应写入日志
- Redactor 继续对 `Authorization`、token-like 字段做脱敏

## Test Plan

- 配置解析测试：
  - `auth.enabled=false` 时无 verifier
  - `jwt-jwks` 模式必填字段校验
  - `introspection` 模式必填字段校验
- server 装配测试：
  - `stdio` 不启用 auth
  - `streamable-http + auth.enabled=true` 会注入 `AuthSettings` 和 `token_verifier`
- verifier 测试：
  - JWT 模式：
    - 合法 token 通过
    - 过期 token 拒绝
    - issuer 不匹配拒绝
    - audience/resource 不匹配拒绝
    - scope 正确解析
  - Introspection 模式：
    - `active=false` 拒绝
    - endpoint 非 200 拒绝
    - audience/resource 不匹配拒绝
    - scope 正确解析
- context 映射测试：
  - `stdio` 仍走开发态上下文
- 授权链测试：
  - 入口级 scope 不满足 -> HTTP 403
  - HTTP 认证成功后，不因 token 缺少工具级 `stock:*` scope 而被 `PolicyEngine` 拒绝
  - destructive 工具无 approval -> 仍拒绝
- manifest 测试：
  - HTTP + auth 模式下 manifest 反映 `auth_enabled/auth_mode/verification`

## Assumptions

- 这次重新设计只覆盖 **OAuth2 Resource Server**，不承担授权服务器职责。
- `stdio` 入口继续保留免认证开发模式。
- 需要同时兼容 JWT/JWKS 与 introspection，因此 verifier 必须抽象化。
- 工具级 `required_scopes` 继续保留在内部授权模型中，但 HTTP 路径不再从 OAuth2 token scope 推导工具权限。
- destructive approval 仍为独立治理能力，不由 OAuth2 自动放行。
