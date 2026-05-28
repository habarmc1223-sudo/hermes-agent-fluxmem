---
name: hermes-programmer
description: DeepSeek-powered coding executor agent. Build features, debug issues, refactor code, review PRs, write tests. Delegates coding subtasks through structured prompts with file context injection.
version: 1.0.0
provider: deepseek
model: deepseek-chat
fallback: openrouter
---

# Hermes Programmer Agent

DeepSeek-powered autonomous coding agent for Hermes workflow.

## Capabilities

- **Build Feature**: generate production code from spec, following project patterns
- **Debug Issue**: root-cause analysis with minimal fix proposals
- **Refactor**: improve code quality without changing behavior
- **Code Review**: find bugs, security issues, logic errors
- **Write Tests**: comprehensive test coverage matching existing patterns

## Usage

```
/hermes coder build "Add user logout endpoint" --files src/auth.ts:100:200
/hermes coder debug "Null pointer in checkout flow" --files src/checkout.ts
/hermes coder refactor "Extract payment logic from controller" --files src/payment.ts
/hermes coder review "PR #42 changes" --files pr_diff.txt
/hermes coder test "Unit tests for auth middleware" --files src/auth.ts
```

## Configuration

Uses `config/providers.yaml` for multi-provider routing:
- Primary: DeepSeek (deepseek-chat / deepseek-coder)
- Fallback: OpenRouter (deepseek/deepseek-chat)
- Local: Ollama (codellama / llama3)

Circuit breaker: 5 failures → 300s cooldown before retry.

## Integration

- Hermes workflow: receives task via message-passing, returns TaskResult
- Docker: runs as `hermes` service in docker-compose.yml
- Telegram: `/coder` command routes to this agent via bot
