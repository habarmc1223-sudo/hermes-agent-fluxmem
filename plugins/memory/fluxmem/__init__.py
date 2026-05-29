"""
FluxMem — heterogeneous graph memory with continuous evolution.

Реализует MemoryProvider интерфейс Hermes Agent.

FluxMem строит направленный взвешенный граф из сообщений пользователя:
- ENTITY / FACT / TASK / PROCEDURE / CONTEXT / PREFERENCE / OUTCOME / PATTERN
- Рёбра с типами: RELATES_TO, ENABLES, PRODUCES, CONFLICTS_WITH, ABSTRACTS, REFINED_BY
- Три стадии эволюции: Binding → Refinement → Consolidation
- Decay неподтверждённых узлов
- Procedural Index для автозапуска процедур
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# ── Tool schemas ──────────────────────────────────────────────────────────────

MEMORY_SCHEMA = {
    "name": "fluxmem_memory",
    "description": (
        "Retrieve or update your FluxMem graph memory. "
        "Omit `query` to see the active graph summary. "
        "Pass `query` to search for relevant nodes. "
        "Pass `export=True` to get full JSON export. "
        "Pass `node_id` to inspect a specific node."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query to find relevant memory nodes. Omit for summary.",
            },
            "export": {
                "type": "boolean",
                "description": "If True, return the full graph as JSON.",
            },
            "node_id": {
                "type": "integer",
                "description": "Inspect a specific node by its ID.",
            },
        },
        "required": [],
    },
}

GRAPH_SCHEMA = {
    "name": "fluxmem_graph",
    "description": (
        "Show the top connections in your FluxMem graph. "
        "Use when you want to understand relationships between memory nodes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Number of top connections to show (default 10).",
            },
        },
        "required": [],
    },
}

PROCEDURES_SCHEMA = {
    "name": "fluxmem_procedures",
    "description": (
        "List the Procedural Index — all saved reusable procedures "
        "that were consolidated from repeated patterns. "
        "MATURE procedures auto-activate when triggers match."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["MATURE", "DEVELOPING", "DEPRECATED"],
                "description": "Filter by procedure status. Omit to show all.",
            },
        },
        "required": [],
    },
}

ALL_TOOL_SCHEMAS = [MEMORY_SCHEMA, GRAPH_SCHEMA, PROCEDURES_SCHEMA]

# ── Default config ───────────────────────────────────────────────────────────

_DEFAULT_DB_DIR = "fluxmem"
_DEFAULT_DB_NAME = "fluxmem.db"


class FluxMemMemoryProvider(MemoryProvider):
    """FluxMem — heterogeneous graph memory for Hermes Agent."""

    def __init__(self):
        self._graph = None          # MemoryGraph instance
        self._engine = None         # FluxMemEngine instance
        self._hermes_home = ""
        self._db_path = ""
        self._user_id = "default"
        self._enabled = False

        # Background evolution thread state
        self._evolution_queue: List[str] = []
        self._queue_lock = threading.Lock()
        self._evolution_thread: Optional[threading.Thread] = None
        self._evolve_running = False

        # Предзагруженный контекст для prefetch()
        self._prefetched_context = ""
        self._prefetch_lock = threading.Lock()

        # Turn tracking
        self._turn_count = 0

    @property
    def name(self) -> str:
        return "fluxmem"

    def _is_unittest_env(self) -> bool:
        """Detect test environment so we skip blocking/thread ops."""
        import sys
        return "pytest" in sys.modules or bool(os.environ.get("HERMES_TESTING", ""))

    # ── Availability ──────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """FluxMem always available — no external services needed."""
        return True

    # ── Config ────────────────────────────────────────────────────────────

    def get_config_schema(self):
        return [
            {
                "key": "enabled",
                "description": "Enable FluxMem graph memory",
                "default": True,
                "type": "bool",
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_path = Path(hermes_home) / "fluxmem.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                pass
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2))

    # ── Initialization ────────────────────────────────────────────────────

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize FluxMem: create DB, schema, start background thread."""
        hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        self._hermes_home = hermes_home

        # Config
        config_path = Path(hermes_home) / "fluxmem.json"
        enabled = True
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text())
                enabled = cfg.get("enabled", True)
            except Exception:
                pass

        if not enabled:
            logger.debug("FluxMem disabled in config")
            return

        # Create DB directory
        db_dir = Path(hermes_home) / _DEFAULT_DB_DIR
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_dir / _DEFAULT_DB_NAME)

        # User ID (use user_id from kwargs, or default)
        self._user_id = kwargs.get("user_id", "default") or "default"

        # Init graph
        from plugins.memory.fluxmem.graph import MemoryGraph
        self._graph = MemoryGraph(self._db_path)
        self._graph.init_schema()

        # Init engine
        from plugins.memory.fluxmem.engine import FluxMemEngine
        self._engine = FluxMemEngine(self._graph)

        self._enabled = True

        # Start background evolution thread
        if not self._is_unittest_env():
            self._evolve_running = True
            self._evolution_thread = threading.Thread(
                target=self._evolution_worker,
                daemon=True,
                name="fluxmem-evolution",
            )
            self._evolution_thread.start()
            logger.debug(
                "FluxMem initialized: db=%s user=%s",
                self._db_path, self._user_id,
            )

    # ── Background evolution worker ───────────────────────────────────────

    def _evolution_worker(self) -> None:
        """Background thread: consume evolution_queue and run stages."""
        while self._evolve_running:
            text = None
            with self._queue_lock:
                if self._evolution_queue:
                    text = self._evolution_queue.pop(0)
            if text:
                try:
                    self._engine.evolve(text, self._user_id)
                except Exception as exc:
                    logger.debug("FluxMem evolution worker error: %s", exc)
            else:
                time.sleep(1)

    # ── System prompt block ───────────────────────────────────────────────

    def system_prompt_block(self) -> str:
        return (
            "\n──────────────────────────────────────────────\n"
            "## FluxMem — графовая память\n"
            "Ваша память — это не список прошлых сообщений, "
            "а направленный взвешенный граф узлов:\n\n"
            "- **ENTITY**: люди, проекты, инструменты, места\n"
            "- **FACT**: конкретные факты\n"
            "- **TASK**: задачи и цели\n"
            "- **PROCEDURE**: повторяемые стратегии (зреют из PATTERN)\n"
            "- **CONTEXT**: ситуативный контекст\n"
            "- **PREFERENCE**: стиль, формат, глубина\n"
            "- **OUTCOME**: результаты и фидбек\n"
            "- **PATTERN**: повторяющиеся комбинации\n\n"
            "Типы связей: RELATES_TO, ENABLES, PRODUCES, CONFLICTS_WITH, "
            "ABSTRACTS, REFINED_BY\n\n"
            "Перед ответом используйте `fluxmem_memory(query=...)` для "
            "поиска релевантных узлов.\n"
            "Используйте `fluxmem_procedures()` для проверки Procedural Index.\n"
            "Не упоминайте ID узлов или силу связей в ответе пользователю.\n"
            "──────────────────────────────────────────────"
        )

    # ── Prefetch ──────────────────────────────────────────────────────────

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return cached context from background prefetch, or do sync lookup."""
        with self._prefetch_lock:
            cached = self._prefetched_context
            self._prefetched_context = ""

        if cached:
            return cached

        # Sync fallback
        if not self._enabled or not self._engine:
            return ""

        try:
            return self._engine.get_context_for_query(query, self._user_id)
        except Exception as exc:
            logger.debug("FluxMem prefetch error: %s", exc)
            return ""

    # ── Sync turn (evolution trigger) ────────────────────────────────────

    def sync_turn(self, user_content: str, assistant_content: str,
                  *, session_id: str = "") -> None:
        """Evolve graph from user message."""
        if not self._enabled:
            return
        # Queue evolution in background thread
        with self._queue_lock:
            self._evolution_queue.append(user_content)
            # Keep queue bounded
            if len(self._evolution_queue) > 10:
                self._evolution_queue = self._evolution_queue[-10:]

    # ── Tool schemas ──────────────────────────────────────────────────────

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return ALL_TOOL_SCHEMAS

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any],
                         **kwargs) -> str:
        if not self._enabled or not self._graph:
            return tool_error("FluxMem not initialized")

        handlers = {
            "fluxmem_memory": self._handle_memory,
            "fluxmem_graph": self._handle_graph,
            "fluxmem_procedures": self._handle_procedures,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return tool_error(f"Unknown tool: {tool_name}")

        try:
            result = handler(args)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            logger.error("FluxMem tool error: %s", exc)
            return tool_error(str(exc))

    def _handle_memory(self, args: Dict) -> Dict:
        query = args.get("query", "")
        export = args.get("export", False)
        node_id = args.get("node_id")

        if export:
            return self._graph.export_json(self._user_id)

        if node_id:
            node = self._graph.get_node(node_id)
            if not node:
                return {"error": f"Node #{node_id} not found"}
            edges = self._graph.get_edges(node_id)
            return {"node": node, "edges": edges[:20]}

        if query:
            nodes = self._graph.search_nodes(query, self._user_id, limit=10)
            return {
                "query": query,
                "results": [
                    {
                        "id": n["id"],
                        "type": n["type"],
                        "label": n["label"],
                        "strength": round(n["strength"], 2),
                    }
                    for n in nodes
                ],
                "count": len(nodes),
            }

        # Summary
        stats = self._graph.get_stats(self._user_id)
        nodes = self._graph.get_user_nodes(self._user_id, limit=8)
        return {
            "summary": True,
            "stats": stats,
            "top_nodes": [
                {
                    "id": n["id"],
                    "type": n["type"],
                    "label": n["label"],
                    "strength": round(n["strength"], 2),
                }
                for n in nodes
            ],
        }

    def _handle_graph(self, args: Dict) -> Dict:
        limit = args.get("limit", 10)
        nodes = self._graph.get_user_nodes(self._user_id, limit=limit)
        connections = []
        for n in nodes:
            edges = self._graph.get_edges(n["id"], limit=5)
            for e in edges[:3]:
                neighbor_id = e["source_id"] if e["target_id"] == n["id"] else e["target_id"]
                connections.append({
                    "from": {"id": n["id"], "label": n["label"], "type": n["type"]},
                    "to_id": neighbor_id,
                    "edge_type": e["type"],
                    "strength": round(e["strength"], 2),
                })
        return {
            "connections": connections[:30],
            "count": min(len(connections), 30),
        }

    def _handle_procedures(self, args: Dict) -> Dict:
        status_filter = args.get("status")
        procs = self._graph.list_procedures(self._user_id)
        if status_filter:
            procs = [p for p in procs if p["status"] == status_filter]
        return {
            "procedures": [
                {
                    "id": p["id"],
                    "codename": p["codename"],
                    "status": p["status"],
                    "strength": round(p["strength"], 2),
                    "trigger": json.loads(p["trigger_conditions"]),
                    "steps": json.loads(p["core_steps"]),
                }
                for p in procs
            ],
            "count": len(procs),
        }

    # ── Shutdown ─────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        self._evolve_running = False
        if self._evolution_thread and self._evolution_thread.is_alive():
            self._evolution_thread.join(timeout=3)
        logger.debug("FluxMem shutdown complete")

    # ── Session end hook ─────────────────────────────────────────────────

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """На завершении сессии: принудительная консолидация."""
        if not self._enabled or not self._engine:
            return
        try:
            # Финальное обрезание
            self._graph.prune_edges()
            # Попытка консолидации
            self._engine.stage_3_consolidation(self._user_id)
            logger.debug("FluxMem on_session_end: consolidation attempt done")
        except Exception as exc:
            logger.debug("FluxMem on_session_end error: %s", exc)
