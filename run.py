import signal
import os
import shutil
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROCESSES: list[subprocess.Popen] = []


def load_local_environment() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        lines = []
    else:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
            value = value[1:-1]
        if name and name.replace("_", "").isalnum():
            os.environ.setdefault(name, value)
    for google_name, gmail_name in (
        ("GOOGLE_CLIENT_ID", "GMAIL_CLIENT_ID"),
        ("GOOGLE_CLIENT_SECRET", "GMAIL_CLIENT_SECRET"),
        ("GOOGLE_REDIRECT_URI", "GMAIL_REDIRECT_URI"),
    ):
        if not os.getenv(gmail_name) and os.getenv(google_name):
            os.environ[gmail_name] = os.environ[google_name]


def ensure_gmail_token_key() -> None:
    os.environ.setdefault("GMAIL_REDIRECT_URI", "http://127.0.0.1:8012/gmail/callback")
    if os.getenv("GMAIL_TOKEN_ENCRYPTION_KEY"):
        if len(os.environ["GMAIL_TOKEN_ENCRYPTION_KEY"]) < 32:
            raise ValueError("GMAIL_TOKEN_ENCRYPTION_KEY must contain at least 32 characters.")
        return
    key_path = Path.home() / ".ai-tech-consultant" / "gmail-token.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        os.environ["GMAIL_TOKEN_ENCRYPTION_KEY"] = key_path.read_text(encoding="utf-8").strip()
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as key_file:
            key_file.write(secrets.token_urlsafe(48))
        os.environ["GMAIL_TOKEN_ENCRYPTION_KEY"] = key_path.read_text(encoding="utf-8").strip()
    if len(os.environ["GMAIL_TOKEN_ENCRYPTION_KEY"]) < 32:
        raise ValueError("The saved Gmail token encryption key is invalid.")


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


def free_ports(*ports: int) -> None:
    for port in ports:
        try:
            output = subprocess.check_output(
                f"netstat -ano | findstr :{port}",
                shell=True,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in output.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid = int(parts[-1])
                    if pid != os.getpid():
                        subprocess.run(f"taskkill /PID {pid} /F", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


if __name__ == "__main__":
    load_local_environment()
    ensure_gmail_token_key()
    free_ports(8010, 8011, 8012, 8081)
    time.sleep(1)
    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    service_token = os.getenv("KNOWLEDGE_SERVICE_TOKEN") or secrets.token_urlsafe(32)
    gmail_session_secret = os.getenv("GMAIL_SESSION_SECRET") or secrets.token_urlsafe(32)
    knowledge_env = {
        "KNOWLEDGE_SERVICE_TOKEN": service_token,
        "GMAIL_SESSION_SECRET": gmail_session_secret,
    }
    start([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8010"], ROOT / "task1-knowledge-brain", knowledge_env)
    start([sys.executable, "-m", "ingestion.scheduler"], ROOT / "task1-knowledge-brain")
    start([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8011"], ROOT / "task3-meeting-to-scope", {"KNOWLEDGE_BRAIN_URL": "http://127.0.0.1:8010", **knowledge_env})
    node = shutil.which("node") or "node"
    start([node, "-r", "ts-node/register", "src/server.ts"], ROOT / "task2-inbox-agent", {
        "PORT": "8012",
        "KNOWLEDGE_BRAIN_URL": "http://127.0.0.1:8010",
        **knowledge_env,
    })
    start([sys.executable, "-m", "http.server", "8081", "--bind", "127.0.0.1", "--directory", str(ROOT / "website")], ROOT)
    print("AI Tech Consultant is starting:")
    print("  Website:       http://127.0.0.1:8081/console.html")
    print("  Knowledge API: http://127.0.0.1:8010/docs")
    print("  Scope API:     http://127.0.0.1:8011/docs")
    print("  Inbox API:     http://127.0.0.1:8012/health")
    try:
        while True:
            for process in PROCESSES:
                code = process.poll()
                if code not in (None, 0):
                    print(f"Process {process.args} exited unexpectedly with code {code}")
                    stop_all()
            time.sleep(1)
    except KeyboardInterrupt:
        stop_all()