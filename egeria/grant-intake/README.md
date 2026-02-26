# Grant Intake — Extended docs

This skill backs the **grant-intake** Seren Cloud agent: a five-phase state machine that collects org info, sectors, logic models, and sector metrics, then exports a Grant Readiness Summary and intake state JSON.

## API contract

- **Request:** `POST` with JSON body `{ "state": <object or null>, "input": { ... } }`.
- **Response:** `{ "state", "prompt", "outputs?" }`. Use `state` on the next request to continue; when phase 5 is confirmed, `outputs` includes `grant-readiness-summary.md` and `intake-state.json`.

## Phase 1 input

- `org_name` (string)
- `mission` (string)
- `key_programs` (array of strings)
- Optional: `docs_analyzed` (array of strings)

## Phase 2 input

- `sectors` (array of sector ids, e.g. `["workforce","health"]`)
- Optional: `custom_sector` — `{ "name": "...", "questions": [{ "id", "text", "hint" }] }`

## Phase 3 input

- `logic_model` — `{ "inputs", "activities", "outputs", "outcomes" }` for the current program
- Or `new_programs` (array of names) if no programs were set in phase 1

## Phase 4 input

- `question_id` (string), `answer` (string)
- Optional: `want_hint` (boolean) for hint text

## Phase 5 input

- `confirmed` (boolean)
- Optional: `edits` (string) if not confirming

## Deploy and invoke

- **Agent ID (current):** `dbe3af9b-088a-4a32-9e6e-a46cbfeece7b`
- **Invoke URL:** `https://api.serendb.com/publishers/seren-cloud/agents/dbe3af9b-088a-4a32-9e6e-a46cbfeece7b/runs`
- **Auth:** `Authorization: Bearer $SEREN_API_KEY`

See [docs/seren/README.md](https://github.com/serendb/seren-sql-interface-402mcp-server/blob/main/docs/seren/README.md) in the source repo for full control-plane and deploy steps.
