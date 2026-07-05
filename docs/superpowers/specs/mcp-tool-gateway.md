# mcpp — MCP Plus

## Problem

第三方 MCP 服务工具好用但设计差。具体痛点：

1. **Tool description 无法修改**。上游 MCP server 的 tool description 是开发者随手写的，质量参差不齐，不能根据使用场景优化。description 是 LLM 选择 tool 时权重最高的可控信号——差的 description 直接导致选错 tool 或漏选。

2. **无法隐藏或映射参数**。上游 tool 暴露了过多参数（内部选项、调试开关、冗余字段），LLM 面对 10 个参数时选择困难。想隐藏 5 个、重命名 3 个、把连续的 `temperature: 0.0-2.0` 转成离散的 `creativity: precise|balanced|creative`——现有方案做不到。

3. **多参数组合爆炸无法剪枝**。上游暴露 `model_tier` × `thinking_effort` 两个参数，5×5=25 种组合，但实际有用的只有 3-4 种。希望用一个下游 enum 参数映射到多组上游参数值（预设），25 种变 3 种。

4. **跨工具引用缺失**。description 里不能自然地引用其他 tool（"先用 XX 获得 YY，再调本工具"），每个 tool 的 description 是孤岛，LLM 不知道工具间的配合关系。

5. **命名冗长**。现有 gateway（如 liteLLM）生成的 tool 名称过长，且没有提供重命名的入口。好的工具名应该像变量名——短且语义化，server 名表分类，tool 名表功能。

6. **不必要的鉴权障碍**。本地使用场景不需要鉴权，但现有方案强制要求。

7. **API key 轮换**。多把 key 轮换使用时，某把 key 调用失败需要手动切换——没有自动暂停不健康 key 并继续轮询其余 key 的机制。

如果什么都不做，就只能接受上游 tool 的原始设计——差的 description、冗余的参数、爆炸的组合、孤立的工具。每次 LLM 选错 tool 或参数，都是在为上游的设计缺陷买单。

## Design Rationale

### 为什么是一个薄代理层而不是重型网关

MetaMCP 和 ContextForge 已经解决了 MCP 聚合问题，但它们引入了 Next.js + PostgreSQL + Redis + OAuth + OIDC + K8s 的完整企业栈。本地使用的场景不需要这些——不需要数据库（配置即 YAML）、不需要认证（本地信任边界）、不需要 K8s（单进程）。

选择 **Python + FastAPI + YAML** 而非 Next.js 重型栈，是因为这个工具的核心逻辑是**数据变换**（schema 改写、tool 重组、参数映射），不是高并发 IO 或分布式协调。FastAPI 一把梭——MCP endpoint + Web 配置 UI + 静态文件——不需要两套框架。

### 为什么 YAML 配置而非数据库

配置的频率低（调一次用很久）、变更需要审计（知道改了什么）、需要可移植（复制到另一台机器）。YAML 文件天然满足：git 版本控制、文本 diff、复制粘贴。数据库是多余的——没有多用户并发写、没有海量配置条目、不需要事务。

### 为什么稳定 key 是 `{upstream}/{tool}`

重命名 tool 时，所有引用该 tool 的 description 需要跟随更新。如果 key 是 `as:`（显示名），改一次显示名就要全局搜索替换——脆且易出错。`{upstream}/{tool}` 是事实来源，不会变：只要上游 server 和 tool 原名不变，key 就稳定。description 里用反引号 `` `key` `` 引用其他 tool，gateway 在渲染时自动替换为当前 `as:` 的值。启动时校验所有引用——引用了不存在或被隐藏的 tool 则报错。

### 为什么反引号引用而非显式声明

显式 `see_also: [...]` 列表的局限性：它只能表达"建议参考"的语义，不能嵌入到 description 的自然语言流中。真实的需求是"在 description 文本的任意位置自然提及另一个 tool"，就像 API 文档里写"请先调用 `/auth` 获取 token"。反引号 `` `key` `` 是此项目引入的约定（借鉴了 Markdown code span 的视觉习惯），而非行业标准——但它简单、人类可读、正则可解析，且不引入新的语法结构。

### 为什么不做自动 description 优化

自动优化需要 eval 框架——生成候选 description、跑触发率测试、对比结果——这是一个独立系统。作为第一版，手工编辑 description 已经解决核心痛点（能改了），优化流程是后续迭代。

### 为什么不做工具合并

多 tool → 一 tool 的合并要求 gateway 理解上游 tool 之间的调用顺序和依赖关系，本质上是 workflow 引擎。这超出了薄代理的职责范围，且可以通过上游自己做 one-tool-multi-action 来解决（如 seed-viz）。先不做，保持设计边界清晰。

### 为什么不做 output 变换

目前痛点在 LLM 调用 tool 之前的决策阶段（选哪个 tool、传什么参数），output 变换解决的是调用之后的体验问题。优先级低，后续迭代。

### 参数变换的设计：连续→离散 + N:1 预设

LLM 不擅长精确数值——它不知道 `temperature: 0.7` 意味着什么，但知道 `creativity: balanced`。把连续值映射为离散 enum，降低了 LLM 的决策难度。

N:1 预设更进一步：多个上游参数（`model` + `effort` + `max_tokens`）被一个下游 enum 参数（`quality: fast|deep`）替代。这是参数组合剪枝——25 种理论组合压缩为 3 种经过验证的预设。

两种变换都是编译期静态映射——YAML 里声明，运行时查表变换。不引入表达式引擎或 DSL。

### 鉴权设计：密钥池轮询 + 健康暂停

上游配置一个 `keys` 数组，轮询使用。某把 key 调用失败（如 401/403/429）时自动暂停该 key，剩余 key 继续轮询。被暂停的 key 通过 UI 手动恢复。不做每日配额追踪、不做滑动窗口限流——v1 只做基于调用结果的健康判断。

## Implementation Notes

- **核心数据流**：`tools/list` → 并发拉取所有上游 tool list → 按 YAML 的 `expose` 过滤 → 重写 name/description/inputSchema → 合并返回。`tools/call` → 解析 tool name → 查 YAML → 参数逆向变换 → 转发上游 → 透传结果。
- **Streamable HTTP**：下游使用 MCP 的 Streamable HTTP 传输（`/mcp`），符合 MCP 2024-11-05+ 规范。这是 MCP 逐步替代 SSE 的方向。
- **配置重载**：通过 `/api/config` POST 触发重载，重建上游连接和 tool 映射表。外部编辑 YAML 后需手动调用 API 或重启进程。不做文件监听——消除并发重载窗口（正在执行的 tools/call 与重载的竞态）。
- **反引号引用校验**：启动时扫描所有 exposed tool 的 description，正则提取 `` `(\S+)` ``，验证每个引用：（1）key 存在於 `expose` 中，（2）对应的 tool 未被 `hide: true`。任一失败则拒绝启动并报告具体错误。
- **Web UI**：`/admin` 路由，纯 HTML + 原生 JS。两个面板：工具面预览（表格展示 tool/description/params）+ YAML 编辑器（textarea 或表单化）。通过 `/api/config` 读写 YAML。不做前端框架。
- **上游连接抽象**：每个 upstream 通过 `transport` 字段声明连接方式。v1 仅实现 `http`（Streamable HTTP + SSE），后续可加 `stdio`。代码层面用 `UpstreamTransport` 协议抽象，`HttpTransport` 是第一个实现——上层 tool 过滤/schema 改写/参数变换/description 替换不感知传输差异。每个 upstream 可配置 `connect_timeout` 和 `read_timeout`（默认 30s/120s），超时后返回明确错误而非挂起。
- **错误传播**：上游返回错误（超时、HTTP 4xx/5xx、MCP 协议错误、auth 失败）时，gateway 透传错误信息并附加 `[gateway] upstream=<name>` 前缀。不包装、不重试（auth 切换在鉴权层处理），让 LLM 看到原始错误上下文。
- **参数变换执行顺序**：重命名（map_from）→ 类型变换（enum/preset）→ 隐藏+默认值注入。这个顺序保证每一步的输入都是上一步的输出。

## Acceptance

1. 配置一个上游 MCP server 的 URL，gateway 启动后，`/mcp` endpoint 返回经过 `expose` 过滤和重写的 tool list——tool 数量正确，name 为 `as:` 指定的值，description 为手写内容。
2. 调用 `tools/call`，参数经过变换后正确转发到上游：隐藏参数自动填入默认值，重命名参数映射回上游原名，enum 值映射回具体值，preset 参数展开为多组上游参数。
3. 启动时，如果 description 中引用了不存在的 tool key，gateway 拒绝启动并输出明确的错误信息（指出哪个 tool 的 description 引用了哪个不存在的 key）。
4. 修改 YAML 中某个 tool 的 `as:` 后，所有 description 中对该 tool 的反引号引用自动反映新名称，无需手动替换。
5. 配置多把 API key 的上游 server，gateway 在调用失败后自动暂停当前 key 并切换到下一把。后续调用使用健康 key。
6. `/admin` 页面可以查看当前暴露的 tool 列表，并编辑 YAML 配置。通过 UI 触发重载后，更新生效且无需重启进程。
