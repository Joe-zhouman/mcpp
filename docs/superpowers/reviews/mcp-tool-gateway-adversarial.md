# Existence Audit — MCP Tool Gateway Spec

**Lean. Ship.**

---

`§鉴权设计: hidden assumption: "本地信任边界" 假定绑定到 127.0.0.1 但未声明。FastAPI 默认监听 0.0.0.0。如不指定 `--bind 127.0.0.1`，同一局域网下的机器可未经鉴权访问 `/admin` 和 `/api/config`——与 spec 承诺的"不需要鉴权"矛盾。在 `curl -X POST http://$(hostname):8000/api/config/reload` 即可触发重载的意义上，这不是信任边界，是敞开的门。`

`§实现: missing: 可观测性。七个子节覆盖了数据流、流式协议、配置重载、反引号校验、管理界面、上游连接管理、错误传播、参数变换顺序——但未定义任何日志作用域、请求追踪或指标。工具调用失败时，无法知道命中哪个上游、应用了什么变换、错误来自哪一层。调试每个失败都需要手动复现。v1 可以没有 tracing，但最少应有每次 tools/call 的结构化日志行（upstream、tool、原始参数、变换后参数、耗时、错误）。`

`§Acceptance #5: missing: 所有 key 均被暂停的终端情形。spec 描述了单把 key 失败→暂停→切换的流程，但未定义当所有 key 都不健康时 gateway 的行为。应当返回明确的 `all_keys_exhausted` 错误，而非试图轮询一个全空的健康池。`

---

**Detail by lens question:**

**Q1 Hidden Assumptions**
- `§鉴权设计`: "本地信任边界" without 127.0.0.1 bind. Gateway defaults to 0.0.0.0; LAN access to `/admin` is unauthenticated and unencrypted.

**Q2 Framing Errors**
- None. The seven pain points now match their proposed solutions. No mis-framed problems.

**Q3 Causal Leaps**
- None. The spec's reasoning chains are valid: key health rotation follows from health-only monitoring, error propagation model follows from thin-proxy design, manual reload follows from concurrency concern.

**Q4 Consensus Blindness**
- None. Backtick-as-reference correctly framed as novel convention, not industry standard. The Python/FastAPI choice is justified by concurrent upstream needs and admin UI.

**Q5 Missing Negative Space**
- Observability: no logging, tracing, or metrics. Debugging requires manual reproduction.
- All-keys-paused edge case: acceptance criteria covers single-key-failure but not terminal failure.
- Line-level config validation: backtick reference validation specified, but YAML structural errors (bad enum mappings, missing fields) not addressed in spec (acceptable for v1 — Pydantic handles this at implementation level).

**Original findings resolved:**
- Pain point 7 / quota mismatch — fixed, pain point now matches solution
- "唯一依据" overstatement — fixed to "权重最高的可控信号"
- Error propagation model — added (§错误传播)
- Upstream timeout — added (§上游连接管理)
- Watchdog hot reload — replaced with manual API trigger (§配置重载)
- Backtick-as-reference framing — honestly framed as novel convention (§反引号引用)
- stdio transport exclusion — rationale added (§上游连接管理)
- Key pool quota design — replaced with health-only rotation (鉴权设计)
