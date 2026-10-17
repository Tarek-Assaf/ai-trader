# AI Trader

Safety-first trading agent scaffold. The default mode is paper trading. Live trading is disabled unless explicitly configured and promoted through the project's safety gates.

## Development

Install Python 3.12 and [uv](https://docs.astral.sh/uv/), then run:

```sh
make dev
make check
```

Never put credentials in source files. Copy `.env.example` only for local development and supply secret values through your environment or secret manager.

