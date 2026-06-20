# ambient-expense-agent

An Ambient Expense Approval Agent built with the Google Agent Development Kit (ADK) that processes incoming expense events, runs security validation (PII redaction and prompt injection checks), evaluates auto-approval rules, performs LLM risk review, and coordinates human-in-the-loop decisions.

## Project Structure

```
ambient-expense-agent/
├── expense_agent/         # Core agent code (formerly app/)
│   ├── agent.py               # Main agent logic & Graph Workflow
│   ├── config.py              # Configuration and thresholds
│   ├── fast_api_app.py        # FastAPI server wrapping the ADK application
│   └── app_utils/             # App utilities (telemetry, typing)
├── tests/                     # Test suite
│   ├── unit/                  # Unit tests (test_dummy)
│   ├── integration/           # Integration & E2E API tests
│   └── eval/                  # Quality evaluation datasets and grade metrics
├── pyproject.toml             # Python dependencies and build package settings
└── Dockerfile                 # Container image specification
```

---

## Requirements

Before you begin, ensure you have:
* **uv**: Python package manager - [Install](https://docs.astral.sh/uv/getting-started/installation/)
* **agents-cli**: Agents CLI - Install with:
  ```bash
  uv tool install google-agents-cli
  ```
* **Google Cloud SDK**: (Optional) For Vertex AI authentication - [Install](https://cloud.google.com/sdk/docs/install)

---

## Setup & Local Development

1. **Install dependencies:**
   ```bash
   agents-cli install
   ```
   *This commands runs `uv sync` to set up the local virtual environment.*

2. **Configure API Keys:**
   Copy the `.env` template (or edit the existing `.env`) and set your Gemini Developer API key:
   ```bash
   GOOGLE_API_KEY=your_gemini_api_key_here
   ```
   *Note: Alternatively, if you wish to use Vertex AI, authenticate via `gcloud auth application-default login` and set your project ID in `.env`.*

3. **Run the local FastAPI server:**
   ```bash
   make run
   ```
   Or:
   ```bash
   uv run uvicorn expense_agent.fast_api_app:app --host 127.0.0.1 --port 8080
   ```

4. **Launch the interactive playground:**
   ```bash
   make playground
   ```
   Or:
   ```bash
   agents-cli playground
   ```

---

## Testing & Quality Evaluation

### Automated Tests
Run standard unit and integration/E2E tests with pytest:
```bash
uv run pytest tests/unit tests/integration
```

### Quality Evaluation Loop (Flywheel)
1. **Generate traces** from the evaluation cases in `tests/eval/datasets/basic-dataset.json`:
   ```bash
   make generate-traces
   ```
2. **Grade traces** using LLM-as-a-judge metrics defined in `tests/eval/eval_config.yaml`:
   ```bash
   make grade
   ```
   *Note: Standard grading calls Gemini in-process. On the free tier, run the standalone grading script with UTF-8 encoding: `uv run python tests/eval/run_grade.py`.*

---

## Docker Deployment

To build and run the container locally:
```bash
docker build -t ambient-expense-agent .
docker run -p 8080:8080 --env-file .env ambient-expense-agent
```
