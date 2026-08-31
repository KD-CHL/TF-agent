# Task 1 implementation report

## Scope

Implemented the standalone map command adapter and strict-schema contract tests in the requested worktree. No Streamlit, text-model, or vision-model separation changes were made.

## Changes

- Added `TF-agent/map_command_adapter.py`.
  - `NormalizedMapCommand` exposes `lat`, `lon`, `zoom`, optional canonical `bounds`, optional `preset`/`label`, `source`, and warnings.
  - `normalize_map_payload()` accepts canonical coordinates, list/object center aliases, and canonical bounds forms.
  - Coordinates and rectangles are finite and range checked; inverted or malformed bounds generate warnings while preserving a valid center.
  - Unknown payload keys generate warnings and are never emitted by `to_command_dict()`.
  - `parse_legacy_map_text()` parses `COMMAND_UPDATE_MAP|lat|lon|zoom` without Streamlit and marks the source as `legacy_text`.
- Extended `MapCommand` with typed optional `MapBounds`; both schemas retain `extra="forbid"` and coordinate/zoom ranges.
- Added unit contract tests covering canonical payloads, center aliases, bounds normalization, invalid bounds, legacy pipe ordering, unknown-field rejection, and invalid centers.

## Verification

The initial RED check was run before implementation and failed during test collection with:

```text
ModuleNotFoundError: No module named 'map_command_adapter'
```

Exact final command:

```bash
conda run -n tf-agent python -m pytest tests/unit/test_map_command_adapter.py tests/unit/test_agent_commands.py tests/unit/test_map_command_protocol.py -q --tb=short -p no:cacheprovider
```

Exact output:

```text
.......................................................................  [100%]
71 passed in 0.33s
```

