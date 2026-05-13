# Demo Runbook: Running the Multi-Agent Workflow

This guide explains how to run the multi-agent demo pipeline against the deployed Trace Aggregator system running on GCP.

## Prerequisites

Ensure the GCP VM deployment is running. You should have:
- VM IP: `136.111.82.105` (or your assigned public IP)
- All services healthy:
  ```bash
  curl http://136.111.82.105:80/          # UI loads
  curl http://136.111.82.105:8000/docs    # API Swagger UI
  curl http://136.111.82.105:9090/healthz # Collector metrics
  ```

## Option A: Run Demo Locally (From Your Laptop)

This approach runs the demo agent workflow on your laptop and sends trace events to the deployed collector.

### 1. Clone/Access the Repo

```bash
cd "/Users/spartan/Downloads/CMPE 273 Enterprise Distributed Systems/Project/CMPE_273_Final/trace-aggregator"
```

### 2. Set Up Python Environment

```bash
# Create virtual environment (if not already done)
python3 -m venv venv
source venv/bin/activate

# Install core dependencies
pip install -r requirements.txt

# Install additional LangGraph/LangChain dependencies (required for demo)
pip install langgraph langchain
```

### 3. Point SDK to Deployed Collector

```bash
export TRACE_COLLECTOR="136.111.82.105:50051"
export TRACE_TENANT_ID="default"
export DEMO_MODE="true"
```

### 4. Run the Demo Pipeline

```bash
python3 -m demo.pipeline
```

**Expected Output:**
```
Running 5 pipeline executions... mode=demo model=mock

--- Run 1/5 ---
→ Running trace_id=fbec65ef-1123-4eaf-a54f-5cd55eccb329
  task: Research the AI observability market and write a Python function that returns 42.
✓ Pipeline finished. Messages: ['start', 'orchestrator: dispatching...', 'research_agent: findings ready', ...]

--- Run 2/5 ---
...

All done. Spans should be in ClickHouse — check the API:
  curl http://localhost:8000/traces
```

The demo runs 5 iterations, each taking ~1-2 seconds (mock LLM mode with synthetic latency). Some runs may fail with expected errors (e.g., hallucinated imports); as long as most traces are captured, the demo succeeded.

### 5. Verify Traces Were Recorded

Open the UI in your browser:
```
http://136.111.82.105/
```

You should see:
- Recent traces listed on the "Traces" page
- Trace DAG showing the 4-agent orchestration
- Span timeline and latency breakdown
- Decision records from the orchestrator, reviewer, etc.

Alternatively, query the API from your terminal:
```bash
curl "http://136.111.82.105:8000/traces?limit=5&tenant_id=default"
```

Or view agent blame breakdown:
```bash
curl "http://136.111.82.105:8000/agents/blame?hours=1"
```

## Option B: Run Demo Inside the Deployed Container

This runs the demo inside the collector's Docker network (no network overhead).

### 1. SSH into the VM

```bash
export ZONE="us-central1-a"
export INSTANCE_NAME="trace-aggregator-vm"

gcloud compute ssh "$INSTANCE_NAME" --zone "$ZONE"
```

### 2. Run Demo Inside the Container Network

```bash
cd ~/trace-aggregator
sudo docker compose -f docker-compose.gcp-vm.yml exec \
  -e TRACE_COLLECTOR="collector:50051" \
  -e TRACE_TENANT_ID="default" \
  -e DEMO_MODE="true" \
  api python3 -m demo.pipeline
```

The collector will receive traces via the internal Docker network (faster, no internet routing).

### 3. Exit SSH and Verify

```bash
exit
```

Then open UI in browser as in Option A, step 5.

## Option C: Run Real LLM Mode (Optional)

To use a real LLM instead of mocks, you need an API key.

### 1. Set Up OpenRouter (Recommended for Testing)

OpenRouter provides API access to many LLM providers. Free tier available for limited testing.

```bash
# Visit https://openrouter.ai and create an account
# Generate an API key from https://openrouter.ai/keys
# Then run:

export TRACE_COLLECTOR="136.111.82.105:50051"
export TRACE_TENANT_ID="default"
export DEMO_MODE="false"
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export OPENAI_MODEL="~google/gemini-flash-latest"  # or your preferred model
export OPENAI_API_KEY="sk-or-v1-xxxxxxxxxxxxx"      # Your OpenRouter key

python3 -m demo.pipeline
```

### 2. Monitor LLM Calls

The demo will make HTTP calls to OpenRouter. Expected output includes actual LLM reasoning:

```
orchestrator: [LLM response from actual model]
research: [Real market analysis]
coder: [Actual code generation]
reviewer: [LLM-generated review]
```

This takes longer (~5-30 seconds depending on LLM latency).

### 3. Examine Token Usage in Traces

Open UI and check the "Trace Details" → "Statistics" section:
- Input tokens: actual tokens sent to LLM
- Output tokens: actual tokens returned by LLM
- Latency: includes LLM API roundtrip time

## Understanding Demo Mode vs. Real LLM

### Demo Mode (DEMO_MODE=true)
- Uses synthetic latency (0.1-0.4 seconds per agent)
- Returns predictable mock outputs for testing
- No API keys needed
- Good for testing infrastructure, UI, and trace flow
- **Recommended for first-time setup verification**

### Real LLM Mode (DEMO_MODE=false)
- Calls actual LLM API (OpenRouter/OpenAI-compatible)
- Requires valid API key and network access
- Real reasoning and outputs (may vary per run)
- Actual token usage recorded
- Good for understanding real-world latency and token distribution

**Setting DEMO_MODE:**
```bash
# Mock mode (default)
export DEMO_MODE="true"
python3 -m demo.pipeline

# Real LLM mode (requires OPENAI_API_KEY)
export DEMO_MODE="false"
export OPENAI_API_KEY="..."
python3 -m demo.pipeline
```

## Query Endpoints

After running the demo, query the API:

### List Recent Traces
```bash
curl "http://136.111.82.105:8000/traces?limit=10&hours=1"
```

### Get Specific Trace Details
```bash
# First get a trace_id from the list above
TRACE_ID="..." 

curl "http://136.111.82.105:8000/traces/$TRACE_ID"
```

### Blame by Agent
```bash
curl "http://136.111.82.105:8000/agents/blame?hours=1"
```

### Raw Spans
```bash
curl "http://136.111.82.105:8000/traces/$TRACE_ID/spans"
```

### Decisions
```bash
curl "http://136.111.82.105:8000/traces/$TRACE_ID/decisions"
```

### SLO Status
```bash
curl "http://136.111.82.105:8000/slo/status"
```

## Viewing Collector Metrics

The collector exposes Prometheus metrics:

```bash
curl "http://136.111.82.105:9090/metrics/prom" | grep collector_spans
```

Key metrics:
- `collector_spans_received`: Total spans ingested
- `collector_decisions_received`: Total decisions ingested
- `collector_writer_flush_success`: Batch flushes to ClickHouse
- `collector_writer_queue_depth`: Pending items awaiting flush

## Troubleshooting

### Issue: Connection refused to collector

**Symptom:**
```
Error: Failed to connect to 136.111.82.105:50051
```

**Solution:**
1. Verify VM is running:
   ```bash
   gcloud compute instances describe trace-aggregator-vm --zone us-central1-a
   ```

2. Check collector service is alive:
   ```bash
   curl http://136.111.82.105:9090/healthz
   ```

3. Verify firewall rule allows port 50051:
   ```bash
   gcloud compute firewall-rules describe trace-aggregator-allow-http
   ```

### Issue: Demo hangs or times out

**Symptom:**
```
Waiting for collector response... [timeout]
```

**Solution:**
1. SSH to VM and check docker logs:
   ```bash
   gcloud compute ssh trace-aggregator-vm --zone us-central1-a --command \
     "sudo docker compose -f docker-compose.gcp-vm.yml logs collector --tail=50"
   ```

2. Verify ClickHouse is healthy:
   ```bash
   gcloud compute ssh trace-aggregator-vm --zone us-central1-a --command \
     "sudo docker compose -f docker-compose.gcp-vm.yml ps"
   ```

### Issue: No traces appear in UI

**Symptom:**
Ran demo successfully, but UI shows empty trace list.

**Solution:**
1. Verify traces are being written to ClickHouse:
   ```bash
   gcloud compute ssh trace-aggregator-vm --zone us-central1-a --command \
     "sudo docker compose -f docker-compose.gcp-vm.yml exec clickhouse clickhouse-client -q 'SELECT COUNT(*) FROM tracing.raw_spans'"
   ```

2. Refresh UI browser tab (may be cached)

3. Check API directly:
   ```bash
   curl "http://136.111.82.105:8000/traces?limit=1"
   ```

### Issue: LLM API calls fail (DEMO_MODE=false)

**Symptom:**
```
HTTPError: LLM HTTP 401: Unauthorized
```

**Solution:**
1. Verify API key is set:
   ```bash
   echo $OPENAI_API_KEY
   ```

2. Check key format matches provider (OpenRouter expects `sk-or-v1-...`)

3. Verify OPENAI_BASE_URL is correct:
   ```bash
   curl -H "Authorization: Bearer $OPENAI_API_KEY" \
     "https://openrouter.ai/api/v1/models" | head -20
   ```

4. Check network access (may need firewall/proxy rules):
   ```bash
   ping openrouter.ai
   ```

### Issue: NameError: name '_REDACT_PATTERNS' is not defined

**Symptom:**
```
NameError: name '_REDACT_PATTERNS' is not defined
All 5/5 runs fail with this error
```

**Solution:**
Rebuild the local venv with latest code:
```bash
source venv/bin/activate
pip install -e .  # Reinstall package in editable mode
# Or run the demo again — the code is already fixed
python3 -m demo.pipeline
```

This error was fixed in `sdk/core.py` by importing `_REDACT_PATTERNS` from `shared.governance`.

## Advanced: Running Multiple Demos in Parallel

To stress-test the system, run multiple demo instances simultaneously:

```bash
# Terminal 1
export TRACE_COLLECTOR="136.111.82.105:50051"
export TRACE_TENANT_ID="tenant_a"
python3 -m demo.pipeline

# Terminal 2 (in parallel)
export TRACE_COLLECTOR="136.111.82.105:50051"
export TRACE_TENANT_ID="tenant_b"
python3 -m demo.pipeline

# Terminal 3 (in parallel)
export TRACE_COLLECTOR="136.111.82.105:50051"
export TRACE_TENANT_ID="tenant_c"
python3 -m demo.pipeline
```

Then monitor collector metrics:
```bash
watch -n 1 'curl -s http://136.111.82.105:9090/metrics/prom | grep "collector_spans_received\|collector_decisions_received"'
```

## Summary

| Scenario | Command |
|----------|---------|
| **Quick demo (local)** | `source venv/bin/activate && TRACE_COLLECTOR=136.111.82.105:50051 python3 -m demo.pipeline` |
| **Demo in container** | `gcloud compute ssh trace-aggregator-vm --zone us-central1-a --command "cd ~/trace-aggregator && sudo docker compose exec -e TRACE_COLLECTOR=collector:50051 api python3 -m demo.pipeline"` |
| **Real LLM mode** | Set `DEMO_MODE=false`, `OPENAI_API_KEY=...`, then `python3 -m demo.pipeline` |
| **View UI** | Open browser to `http://136.111.82.105/` |
| **Query API** | `curl http://136.111.82.105:8000/traces?limit=10` |
| **Check metrics** | `curl http://136.111.82.105:9090/metrics/prom` |

For more information, see the main [README.md](README.md) and [api/main.py](api/main.py) docstrings.
