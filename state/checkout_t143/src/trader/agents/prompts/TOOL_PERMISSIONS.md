# Agent tool permission table (v1)

All tools are read-only. No agent has a tool that places orders, changes settings, edits files, or runs code.

| Agent | Allowed tools |
|---|---|
| Orchestrator | none (code, not an LLM) |
| Researcher | web_search, fetch_url, news_feed, calendar, macro_series |
| Technical Analyst | get_snapshot, get_candles, run_indicator |
| News and Macro Analyst | news_feed, calendar, macro_series, fetch_url |
| Strategist | get_snapshot, query_memory, quick_backtest |
| Critic | query_memory, calendar |
| Performance Analyst | query_trades |
| Reporter | query_trades, make_chart |

Rules: fetched content is always labeled untrusted data. A tool not listed for an agent is denied. Adding a tool or widening a row requires a change request to Claude and a decision entry.
