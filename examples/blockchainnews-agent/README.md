# Blockchain News Agent

A production-like APEX agent that searches for blockchain news using DuckDuckGo.

## How It Works

1. Agent registers on ERC-8004 identity registry
2. Clients create funded APEX jobs with search queries
3. Agent polls for funded jobs, searches news, submits results to IPFS
4. APEX Evaluator handles settlement after liveness period

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env: add PRIVATE_KEY and PINATA_JWT
```

## Usage

```bash
# One-time: Register agent on-chain
python scripts/register.py

# Run the agent server
python scripts/run_agent.py
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /apex/negotiate | Price negotiation |
| POST | /apex/submit | Submit result |
| GET | /apex/job/{id} | Job details |
| GET | /apex/status | Agent status |
| POST | /search | Direct news search (testing) |
| GET | /health | Health check |

## Testing Without APEX

```bash
curl -X POST http://localhost:8003/search \
  -H "Content-Type: application/json" \
  -d '{"query": "BNB Chain news", "max_results": 5}'
```
