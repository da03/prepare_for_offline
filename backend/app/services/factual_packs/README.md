# Factual packs

Curated, source-backed JSON knowledge that answers structured factual questions
deterministically and offline. Each pack matches `factual-pack/v1`:

- `pack_key`, `title`, `as_of`
- `entities`: canonical names, types, and aliases used for disambiguation
- `sources`: default provenance
- `facts`: each with an `id`, `family`, curated `answer`, `triggers`, and
  optional per-fact `sources`

At answer time, `factual_packs.lookup()` matches a question to a fact by trigger
phrase (or, when a pack entity is mentioned, by high token coverage) and returns
the curated answer. This layer takes precedence over the neural answerer so a
hallucinating adapter cannot override a curated fact.

These built-in packs are a starter set. User-prepared packs load from
`<home>/factual_packs` and are evaluated against the country-facts benchmark
(`backend/eval/country_facts`).
