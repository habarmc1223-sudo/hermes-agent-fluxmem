---
name: fluxmem
version: 1.0.0
description: FluxMem — heterogeneous graph memory with continuous evolution
pip_dependencies: []
hooks:
  - on_session_end
---

### FluxMem Memory Provider

FluxMem — графовая память с непрерывной эволюцией для Hermes Agent.

#### Быстрый старт

```yaml
# ~/.hermes/config.yaml
memory:
  provider: fluxmem
  memory_enabled: true
```

#### Как это работает

1. **После каждого ответа** — `sync_turn()` ставит в очередь фоновую эволюцию
2. **Перед каждым запросом** — `prefetch()` достаёт релевантный контекст из графа
3. **3 стадии эволюции:**
   - Stage 1 (Binding): LLM-extract сущностей → узлы + связи
   - Stage 2 (Refinement): [OK]/+0.15, [NO]/-0.20, decay, pruning
   - Stage 3 (Consolidation): PATTERN ≥ 0.85 → PROCEDURE в Procedural Index

#### Инструменты модели

| Инструмент | Назначение |
|-----------|------------|
| `fluxmem_memory(query=...)` | Поиск релевантных узлов |
| `fluxmem_memory(export=True)` | Полный JSON экспорт |
| `fluxmem_memory(node_id=...)` | Просмотр узла |
| `fluxmem_graph(limit=10)` | Связи графа |
| `fluxmem_procedures(status=...)` | Procedural Index |

#### Структура БД

DB: `~/.hermes/fluxmem/fluxmem.db` (SQLite WAL)

- **memory_nodes** — узлы графа (типы: ENTITY, FACT, TASK, PROCEDURE, CONTEXT, PREFERENCE, OUTCOME, PATTERN)
- **memory_edges** — рёбра (типы: RELATES_TO, ENABLES, PRODUCES, CONFLICTS_WITH, ABSTRACTS, REFINED_BY)
- **procedural_index** — зрелые процедуры (MATURE / DEVELOPING / DEPRECATED)

#### Конфигурация

```json
// ~/.hermes/fluxmem.json
{
  "enabled": true
}
```

#### Fallback

Если DeepSeek API недоступен — entity extraction на основе стоп-слов (слова > 3 букв как ENTITY).
