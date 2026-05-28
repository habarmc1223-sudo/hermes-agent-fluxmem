"""Project Context — persistent knowledge about each project."""

from .memory_store import MemoryStore


class ProjectContext:
    """Persistent per-project knowledge base."""

    def __init__(self, project_name: str, store: MemoryStore = None):
        self.project = project_name
        self.store = store or MemoryStore()

    def set(self, key: str, value: str, category: str = "project_knowledge"):
        self.store.set(key, value, category=category, project=self.project)

    def get(self, key: str) -> str:
        return self.store.get(key) or ""

    # ── Architecture ──
    def record_architecture(self, note: str):
        import uuid
        self.set(f"arch:{uuid.uuid4().hex[:8]}", note, category="architecture")

    def get_architecture(self) -> list:
        return self.store.get_by_project(self.project, limit=20)

    # ── Decisions ──
    def record_decision(self, title: str, rationale: str):
        self.set(f"decision:{title}", rationale, category="decisions")

    def get_decisions(self) -> list:
        return [
            e for e in self.store.get_by_project(self.project, limit=100)
            if e["key"].startswith("decision:")
        ]

    # ── Tech Stack ──
    def set_tech_stack(self, stack: str):
        self.set("tech_stack", stack, category="project_profile")

    def get_tech_stack(self) -> str:
        return self.get("tech_stack")

    # ── API Keys / Config Notes (no actual secrets!) ──
    def note_config(self, key: str, description: str):
        self.set(f"config:{key}", description, category="project_config")

    # ── Context Prompt ──
    def get_context_prompt(self) -> str:
        tech = self.get_tech_stack()
        decisions = self.get_decisions()[:5]
        ctx = f"Project: {self.project}\n"
        if tech:
            ctx += f"Stack: {tech}\n"
        if decisions:
            ctx += "Key decisions:\n"
            for d in decisions:
                ctx += f"- {d['key'].replace('decision:','')}: {d['value'][:100]}\n"
        return ctx
