# ml-scientist

A protocol-driven ecosystem for automated scientific experimentation.

## What it is

A set of MCP servers that treat the scientific loop as durable state:

```
hypothesis → designed experiment → execution → observation
→ statistical analysis → belief update → next experiment
```

Any MCP-compatible LLM client can drive experiments through it. The
system records every step — hypotheses, trials, observations, belief
updates, and conclusions — as queryable, provenance-tracked state.

## License

Apache License 2.0. See [LICENSE](LICENSE).
