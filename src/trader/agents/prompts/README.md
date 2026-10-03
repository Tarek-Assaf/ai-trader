# Agent prompts

Files here are versioned instructions. Codex loads them by file name and reads the version from the first heading; it does not edit them. Every agent call logs the prompt file name and version in the audit trail. `common_rules.md` is prepended to every agent prompt. Tool access is defined by `TOOL_PERMISSIONS.md`.
