# AI Tech Consultant

Three local workflows share one product-knowledge API: cited knowledge search, human-reviewed inbox drafts, and meeting-to-scope proposals. The browser workbench is at `http://127.0.0.1:8080/console.html`.

## Setup and run

Requires Python 3.11+, Node.js 20+, and npm. From the repository root:

```powershell
python -m pip install -r requirements.txt
cd task2-inbox-agent
npm install
cd ..
python run.py
```

Open the workbench at `http://127.0.0.1:8081/console.html`. Stop all services with `Ctrl+C`. The launcher starts Task 1 on port 8010, Task 3 on 8011, the TypeScript Inbox API on 8012, and the static website on 8081.

Optional Claude Messages API access uses `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL`. Optional Voyage embeddings use `VOYAGE_API_KEY` and `VOYAGE_EMBEDDING_MODEL` (defaults to `voyage-3-lite`). Without credentials, the project uses deterministic keyword retrieval and offline workflow logic. Set `VISION_ENABLED=false` to skip image calls. Set `ENABLE_DAILY_INGESTION=true` before `python run.py` to start the daily worker; manual ingestion remains available in the workbench. Never commit keys.

Crawler controls include `CRAWL_DELAY_SECONDS` (default `0.5`), `CRAWL_RETRIES` (default `3`), and `CRAWL_TIMEOUT_SECONDS` (default `15`). The persistent sitemap cursor resumes subsequent bounded batches; review the ingestion report before raising the batch size.

## Tests

```powershell
cd task1-knowledge-brain; python -m pytest -q
cd ..\task2-inbox-agent; npm test; npx tsc --noEmit
cd ..\task3-meeting-to-scope; python -m pytest -q
```

The tests use local synthetic fixtures; they do not claim live help-center coverage or model quality. See [DESIGN.md](DESIGN.md) and [EVALS.md](EVALS.md) for architecture and measured limits. The generated meeting transcripts are labeled synthetic, not recorded role-plays.