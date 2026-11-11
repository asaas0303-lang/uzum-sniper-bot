import sys
import os
import time
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "bot_service.log"

def main():
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n=========================================\n")
        f.write(f"[UZUM SNIPER 24/7 DAEMON BOSHLANDI: {time.ctime()}]\n")
        f.write(f"=========================================\n")

    python_exe = sys.executable

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    while True:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as log_f:
                proc = subprocess.Popen(
                    [python_exe, str(BASE_DIR / "bot.py")],
                    cwd=str(BASE_DIR),
                    stdout=log_f,
                    stderr=log_f,
                    env=env,
                )
                proc.wait()
        except Exception as e:
            with open(LOG_FILE, "a", encoding="utf-8") as log_f:
                log_f.write(f"[SUPERVISOR EXCEPTION]: {e}\n")
        time.sleep(3)

if __name__ == "__main__":
    main()
