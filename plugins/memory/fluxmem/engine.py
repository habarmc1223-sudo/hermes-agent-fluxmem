"""
FluxMemEngine — три стадии эволюции графа памяти.

Использует LLM (DeepSeek/OpenAI-compatible) для извлечения сущностей
из текста пользователя и построения графа.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from plugins.memory.fluxmem.graph import (
    CONSOLIDATION_THRESHOLD,
    DECAY_FACTOR,
    FEEDBACK_STRENGTHEN,
    FEEDBACK_WEAKEN,
    NEW_EDGE_STRENGTH,
    NODE_TYPES,
    PRUNE_THRESHOLD,
    MemoryGraph,
)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore

logger = logging.getLogger(__name__)

# ── Default LLM config ───────────────────────────────────────────────────────

_DEFAULT_LLM_BASE = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
_DEFAULT_LLM_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
_DEFAULT_LLM_MODEL = "deepseek-chat"


class FluxMemEngine:
    """Три стадии эволюции графа памяти.

    Стадии:
      1. Initial Binding  — создать/обновить узлы из сообщения пользователя
      2. Feedback Refinement — усилить/ослабить по реакции
      3. Long-term Consolidation — PATTERN → PROCEDURE
    """

    def __init__(self, graph: MemoryGraph):
        self._graph = graph
        self._interaction_count: Dict[str, int] = {}  # user_id → count
        self._llm_client: Optional[Any] = None

    # ── LLM setup ──────────────────────────────────────────────────────────

    def _ensure_llm(self) -> bool:
        """Ленивая инициализация LLM клиента."""
        if self._llm_client is not None:
            return True
        if OpenAI is None:
            logger.warning("FluxMem: openai package not installed — entity extraction disabled")
            self._llm_client = False
            return False
        base_url = os.environ.get("DEEPSEEK_BASE_URL", _DEFAULT_LLM_BASE)
        api_key = os.environ.get("DEEPSEEK_API_KEY", _DEFAULT_LLM_KEY)
        if not api_key:
            logger.warning("FluxMem: no DEEPSEEK_API_KEY — entity extraction disabled")
            self._llm_client = False
            return False
        self._llm_client = OpenAI(base_url=base_url, api_key=api_key)
        return True

    def _get_model(self) -> str:
        return os.environ.get("DEEPSEEK_MODEL", _DEFAULT_LLM_MODEL)

    # ── Entity extraction ──────────────────────────────────────────────────

    def extract_entities(self, user_text: str) -> List[Tuple[str, str]]:
        """
        Извлечение сущностей из текста через DeepSeek.

        Returns:
            Список (тип_узла, label)
        """
        if not self._ensure_llm():
            return self._fallback_extract(user_text)

        # Проверка на команду
        if user_text.startswith("/"):
            cmd = user_text.split()[0].lstrip("/")
            return [("PROCEDURE", cmd)]

        try:
            resp = self._llm_client.chat.completions.create(  # type: ignore
                model=self._get_model(),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Извлеки ключевые сущности из сообщения пользователя. "
                            "Формат ответа: каждая сущность на новой строке: ТИП: label\n"
                            "Типы: ENTITY (люди, проекты, места, инструменты), "
                            "FACT (конкретный факт), "
                            "TASK (задача/цель), "
                            "CONTEXT (контекст/ситуация), "
                            "PREFERENCE (предпочтение/стиль).\n"
                            "Не выдумывай — только то что явно сказано. "
                            "Максимум 5 сущностей. Если нечего извлечь — ответь: none"
                        ),
                    },
                    {"role": "user", "content": user_text},
                ],
                temperature=0.1,
                max_tokens=200,
            )
            content = resp.choices[0].message.content or ""
            if content.strip().lower() == "none":
                return []
            entities = []
            for line in content.strip().split("\n"):
                line = line.strip()
                if ":" in line:
                    etype, _, label = line.partition(":")
                    etype = etype.strip().upper()
                    label = label.strip().strip("\"'")
                    if etype in NODE_TYPES and label:
                        entities.append((etype, label))
            return entities[:5]
        except Exception as exc:
            logger.debug("FluxMem entity extraction failed: %s", exc)
            return self._fallback_extract(user_text)

    def _fallback_extract(self, user_text: str) -> List[Tuple[str, str]]:
        """Простой fallback без LLM — слова > 3 букв как ENTITY."""
        words = user_text.split()
        # Фильтр: не команды, не стоп-слова
        stop_words = {"ok", "okay", "yes", "no", "да", "нет", "good", "bad",
                       "спасибо", "thanks", "пожалуйста", "please",
                       "надо", "нужно", "можно", "хорошо", "ладно", "ага"}
        return [
            ("ENTITY", word.strip(",.!?\"'"))
            for word in words
            if len(word) > 3
            and not word.startswith("/")
            and word.lower() not in stop_words
        ][:3]

    # ── Stage 1: Initial Binding ──────────────────────────────────────────

    def stage_1_binding(
        self, user_id: str, user_text: str,
        entities: List[Tuple[str, str]],
    ) -> List[int]:
        """
        Initial Binding: создаёт/обновляет узлы для сущностей и связывает их.

        Returns:
            Список ID созданных/найденных узлов
        """
        node_ids = []
        for node_type, label in entities:
            if node_type not in NODE_TYPES:
                continue
            existing = self._graph.find_node_by_label(label, user_id, node_type)
            if existing:
                self._graph.update_node_strength(existing["id"], 0.05)
                nid = existing["id"]
            else:
                nid = self._graph.create_node(node_type, label, user_id)
            node_ids.append(nid)

        # Связываем новые узлы между собой (RELATES_TO)
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                self._graph.upsert_edge(
                    node_ids[i], node_ids[j],
                    "RELATES_TO", NEW_EDGE_STRENGTH,
                )

        self._interaction_count[user_id] = self._interaction_count.get(user_id, 0) + 1
        logger.debug(
            "FluxMem Stage1: user=%s nodes=%d entities=%s",
            user_id, len(node_ids), entities,
        )
        return node_ids

    # ── Stage 2: Feedback Refinement ──────────────────────────────────────

    def stage_2_refinement(
        self, user_id: str, user_text: str,
        involved_node_ids: List[int],
    ) -> None:
        """
        Feedback Refinement: анализирует текст на сигналы обратной связи.

        [OK]  — approval → strengthen
        [NO]  — rejection → weaken
        [REP] — repetition → pattern strengthening (через interaction_count)
        """
        text_lower = user_text.lower()
        is_ok = any(w in text_lower for w in [
            "ok", "хорошо", "отлично", "спасибо", "thanks",
            "да", "именно", "верно", "👍", "✅",
            "супер", "замечательно", "perfect", "great", "exactly",
        ])
        is_no = any(w in text_lower for w in [
            "нет", "не то", "неправильно", "плохо",
            "другое", "неверно", "👎", "❌",
            "не подходит", "wrong", "bad", "no",
        ])

        delta = 0.0
        signal = None
        if is_ok:
            delta = FEEDBACK_STRENGTHEN
            signal = "OK"
        elif is_no:
            delta = FEEDBACK_WEAKEN
            signal = "NO"

        if delta != 0.0 and involved_node_ids:
            for nid in involved_node_ids:
                self._graph.update_node_strength(nid, delta)
                edges = self._graph.get_edges(nid)
                for edge in edges:
                    self._graph.adjust_edge_strength(edge["id"], delta * 0.5)
            logger.debug(
                "FluxMem Stage2: user=%s signal=%s delta=%.2f nodes=%d",
                user_id, signal, delta, len(involved_node_ids),
            )

        # Decay: раз в 5 взаимодействий
        self._interaction_count[user_id] = self._interaction_count.get(user_id, 0) + 1
        if self._interaction_count[user_id] % 5 == 0:
            decayed = self._graph.apply_decay(user_id)
            if decayed:
                logger.debug("FluxMem: decayed %d nodes (user=%s)", decayed, user_id)

        # Prune: раз в 10 взаимодействий
        if self._interaction_count[user_id] % 10 == 0:
            pruned = self._graph.prune_edges()
            if pruned:
                logger.debug("FluxMem: pruned %d edges (user=%s)", pruned, user_id)

    # ── Stage 3: Long-term Consolidation ──────────────────────────────────

    def stage_3_consolidation(self, user_id: str) -> Optional[Dict]:
        """
        Stage 3 Consolidation: PATTERN узлы, достигшие CONSOLIDATION_THRESHOLD,
        превращаются в PROCEDURE в Procedural Index.
        """
        patterns = self._graph.get_user_nodes(user_id, "PATTERN", limit=20)
        for p_node in patterns:
            if p_node["strength"] >= CONSOLIDATION_THRESHOLD:
                meta = json.loads(p_node.get("metadata", "{}"))
                codename = meta.get("codename", p_node["label"].lower().replace(" ", "_"))
                trigger = meta.get("trigger", {})
                steps = meta.get("steps", ["Выполнить действие"])

                proc_id = self._graph.create_procedure(codename, trigger, steps, user_id)
                self._graph.set_procedure_status(proc_id, "MATURE")

                logger.info(
                    "FluxMem Stage3: PATTERN#%d(%s) → PROCEDURE#%d(%s) [MATURE]",
                    p_node["id"], p_node["label"], proc_id, codename,
                )
                return {
                    "pattern_node": p_node,
                    "procedure_id": proc_id,
                    "codename": codename,
                }
        return None

    # ── Execute evolution pipeline ────────────────────────────────────────

    def evolve(self, user_text: str, user_id: str = "default") -> Dict:
        """
        Полный цикл эволюции за один вызов.
        Stage 1 + 2 + 3.

        Returns:
            Dict с результатами эволюции
        """
        result = {"entities": [], "node_ids": [], "consolidated": None}

        entities = self.extract_entities(user_text)
        result["entities"] = entities

        if entities:
            node_ids = self.stage_1_binding(user_id, user_text, entities)
            result["node_ids"] = node_ids

            self.stage_2_refinement(user_id, user_text, node_ids)

            consolidated = self.stage_3_consolidation(user_id)
            result["consolidated"] = consolidated

        return result

    # ── Context retrieval ─────────────────────────────────────────────────

    def get_context_for_query(
        self, query: str, user_id: str = "default", max_nodes: int = 8,
    ) -> str:
        """
        Извлечь релевантный контекст из графа для запроса.

        1. Ищет узлы по ключевым словам из query
        2. BFS traversal depth=1 для соседей
        3. Проверяет Procedural Index
        4. Форматирует в [MEMORY CONTEXT] блок

        Returns:
            Форматированный контекстный блок или пустую строку
        """
        # Извлекаем ключевые слова
        words = [w.strip(",.!?\"'").lower() for w in query.split() if len(w) > 3
                 and not w.startswith("/")]
        if not words:
            return ""

        # Проверка процедурного индекса
        if query.startswith("/"):
            cmd = query.split()[0].lstrip("/")
            proc = self._graph.find_matching_procedure(
                {"command": cmd}, user_id
            )
            if proc:
                steps_str = ", ".join(json.loads(proc["core_steps"])[:4])
                return (
                    f"[MEMORY CONTEXT]\n"
                    f"  Active Procedure: {proc['codename']} [{proc['status']}]\n"
                    f"  Steps: {steps_str}\n"
                )

        # Traversal графа
        nodes = self._graph.traverse(words, user_id, max_depth=2, max_nodes=max_nodes)
        if not nodes:
            return ""

        lines = ["[MEMORY CONTEXT]"]
        for n in nodes:
            strength_bar = "█" * int(n["strength"] * 10) + "░" * (10 - int(n["strength"] * 10))
            lines.append(
                f"  [{n['type']}] {n['label'][:50]}  {strength_bar} {n['strength']:.2f}"
            )

        return "\n".join(lines)
