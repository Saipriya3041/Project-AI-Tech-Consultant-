import signal
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROCESSES: list[subprocess.Popen] = []

def start(command: list[str], cwd: Path, extra_env: dict[str, str] | None = None) -> None:
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update(extra_env)
    PROCESSES.append(subprocess.Popen(command, cwd=cwd, env=env))


def stop_all(*_args) -> None:
    for process in PROCESSES:
        if process.poll() is None:
            process.terminate()
    for process in PROCESSES:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    start([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8010"], ROOT / "task1-knowledge-brain")
    if os.getenv("ENABLE_DAILY_INGESTION", "false").lower() == "true":
        start([sys.executable, "-m", "ingestion.scheduler"], ROOT / "task1-knowledge-brain")
    start([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8011"], ROOT / "task3-meeting-to-scope", {"KNOWLEDGE_BRAIN_URL": "http://127.0.0.1:8010"})
    node = shutil.which("node") or "node"
    start([node, "-r", "ts-node/register", "src/server.ts"], ROOT / "task2-inbox-agent", {"PORT": "8012", "KNOWLEDGE_BRAIN_URL": "http://127.0.0.1:8010"})
    start([sys.executable, "-m", "http.server", "8081", "--bind", "127.0.0.1", "--directory", str(ROOT / "website")], ROOT)
    print("AI Tech Consultant is starting:")
    print("  Website:       http://127.0.0.1:8081/console.html")
    print("  Knowledge API: http://127.0.0.1:8010/docs")
    print("  Scope API:     http://127.0.0.1:8011/docs")
    print("  Inbox API:     http://127.0.0.1:8012/health")
    try:
        while True:
            if any(process.poll() not in (None, 0) for process in PROCESSES):
                stop_all()
            time.sleep(1)
    except KeyboardInterrupt:
        stop_all()