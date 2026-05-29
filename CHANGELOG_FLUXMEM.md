# Changelog — hermes-agent-fluxmem

Форк [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) с добавлением
FluxMem — графовой памяти с непрерывной эволюцией.

## v2026.5.29 — FluxMem

### Added
- **plugins/memory/fluxmem/** — новый MemoryProvider плагин:
  - `graph.py` — MemoryGraph (SQLite WAL, 8 типов узлов, 6 типов рёбер, BFS traversal, decay, pruning)
  - `engine.py` — FluxMemEngine (3 стадии эволюции, entity extraction через DeepSeek)
  - `__init__.py` — FluxMemMemoryProvider (MemoryProvider интерфейс, background evolution worker)
  - `plugin.yaml` — метаданные плагина
  - `README.md` — документация
- **skills/fluxmem-system-prompt/** — системный промпт для модели с описанием графа
- **~/.hermes/config.yaml** — `memory.provider: fluxmem` (активация)

### Changed
- Настройки памяти в `~/.hermes/config.yaml`:
  - `memory.provider: ''` → `memory.provider: 'fluxmem'`

### How to enable
```yaml
# ~/.hermes/config.yaml
memory:
  provider: fluxmem
  memory_enabled: true
```

### Подробнее
См. [plugins/memory/fluxmem/README.md](plugins/memory/fluxmem/README.md)
