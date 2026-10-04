# Agent prompts

Files here are versioned instructions. Codex loads them by file name and reads the version from the first heading; it does not edit them. Every agent call logs the prompt file name and version in the audit trail. `common_rules.md` is prepended to every agent prompt. Tool access is defined by `TOOL_PERMISSIONS.md`.

Roles in `config/llm.yaml` map to prompt files: router_intent, data_summarizer, news_macro_analyst, technical_analyst, strategist, critic, performance_analyst, reporter_commentary. Instrument playbooks (`playbook_*.md`) are appended to the analyst and strategist prompts for that instrument. Any prompt change bumps its version and needs a decision entry.
