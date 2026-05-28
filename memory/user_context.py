"""User Context — persistent long-term memory about the user."""

from .memory_store import MemoryStore


class UserContext:
    """Persistent user profile and preference storage."""

    def __init__(self, user_id: int = 1, store: MemoryStore = None):
        self.user_id = user_id
        self.store = store or MemoryStore()
        self._namespace = f"user:{user_id}"

    def _key(self, field: str) -> str:
        return f"{self._namespace}:{field}"

    # ── Profile ──
    def set_name(self, name: str):
        self.store.set(self._key("name"), name, category="user_profile", importance=10)

    def set_role(self, role: str):
        self.store.set(self._key("role"), role, category="user_profile", importance=8)

    def get_name(self) -> str:
        return self.store.get(self._key("name")) or ""

    # ── Preferences ──
    def set_preference(self, key: str, value: str):
        self.store.set(self._key(f"pref:{key}"), value, category="user_preferences", importance=5)

    def get_preference(self, key: str) -> str:
        return self.store.get(self._key(f"pref:{key}")) or ""

    def set_language(self, lang: str):
        self.set_preference("language", lang)

    def set_timezone(self, tz: str):
        self.set_preference("timezone", tz)

    # ── Knowledge ──
    def remember_fact(self, fact: str, importance: int = 5):
        import uuid
        self.store.set(
            self._key(f"fact:{uuid.uuid4().hex[:8]}"),
            fact, category="user_knowledge", importance=importance,
        )

    def get_all_facts(self, limit: int = 50) -> list:
        return [
            e for e in self.store.get_by_category("user_knowledge", limit=200)
            if e["key"].startswith(self._namespace)
        ][:limit]

    # ── Projects ──
    def add_project(self, project_name: str):
        projects = self.get_projects()
        if project_name not in projects:
            projects.append(project_name)
            import json
            self.store.set(self._key("projects"), json.dumps(projects), category="user_profile")

    def get_projects(self) -> list:
        import json
        raw = self.store.get(self._key("projects")) or "[]"
        return json.loads(raw)

    # ── Context Summary ──
    def get_context_prompt(self) -> str:
        """Build a context prompt for agent conversations."""
        name = self.get_name()
        projects = self.get_projects()
        facts = self.get_all_facts(10)
        prefs = self.store.get_by_category("user_preferences", 10)

        ctx = f"User: {name or 'Unknown'}\n"
        if projects:
            ctx += f"Projects: {', '.join(projects)}\n"
        if facts:
            ctx += "Key facts:\n"
            for f in facts[:5]:
                ctx += f"- {f['value'][:100]}\n"
        if prefs:
            ctx += "Preferences: " + ", ".join(
                f"{p['key'].split(':')[-1]}={p['value']}" for p in prefs[:5]
            )
        return ctx
