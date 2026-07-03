# Obsidian knowledge retrieval

Hermes indexes only explicitly supplied Markdown roots. Retrieved text is
untrusted evidence and every result includes an Obsidian-compatible citation.

```bash
python scripts/obsidian_knowledge.py sync ~/.hermes/knowledge/wb-company
python scripts/obsidian_knowledge.py search "Какие решения приняты по рекламе?"
python scripts/obsidian_knowledge.py evaluate eval/knowledge.json
```

Without `OPENAI_API_KEY`, search uses SQLite FTS5. With the key present it also
uses `text-embedding-3-small` and fuses lexical and semantic ranking. Query
traces store only a SHA-256 query hash, latency, result count, and strategies;
the query and note content are not copied into telemetry.

Run `sync` after knowledge publication. The command is incremental: unchanged
files are skipped, changed files are replaced atomically, and missing files are
removed from the index. The Hermes search tool performs the same incremental
sync before retrieval. Set `HERMES_KNOWLEDGE_ROOTS` (OS path-separated) to
override its default `~/.hermes/knowledge/wb-company` root.

Never point it at the whole personal vault on a shared
or business VPS; pass only roots authorized for that host.
