# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, or
- **`CONTEXT-MAP.md`** at the repo root if it exists: it points at one `CONTEXT.md` per context. Read each one relevant to the topic.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in. In multi-context repos, also check context-scoped ADR directories.

If any of these files don't exist, proceed silently. Don't flag their absence; the domain-modeling skill creates them lazily when terms or decisions are resolved.

## File structure

This is a single-context repo: root `CONTEXT.md` and system-wide `docs/adr/`.

## Use the glossary's vocabulary

When an output names a domain concept, use the term defined in `CONTEXT.md`. If the concept isn't there, note the vocabulary gap for domain modeling.

## Flag ADR conflicts

If an output contradicts an existing ADR, surface it explicitly rather than silently overriding it.
