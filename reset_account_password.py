import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "task1-knowledge-brain"))

from app.auth import reset_local_account_password

if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "somishettysaipriya15@gmail.com"
    password = sys.argv[2] if len(sys.argv) > 2 else "Password123456"
    if len(password) < 12:
        print("Error: Password must be at least 12 characters.")
        sys.exit(1)
    success = reset_local_account_password(email, password)
    if success:
        print(f"Successfully reset password for {email} to: {password}")
    else:
        print(f"User {email} not found.")
