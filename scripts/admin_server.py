#!/usr/bin/env python3
"""니콘 시세 트래커 어드민 서버.

사용법:
    python scripts/admin_server.py [--port 8080]
    브라우저에서 http://localhost:8080/admin.html 접속
"""

import json
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "products.yaml"
BACKUP_DIR = PROJECT_ROOT / "config" / "backups"
LOCAL_HOSTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


class AdminHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def do_GET(self):
        if not self.is_local_client():
            self.send_error(403, "Admin server is local-only")
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/session":
            self.send_session()
        elif parsed.path == "/api/catalog":
            self.send_catalog()
        else:
            super().do_GET()

    def do_POST(self):
        if not self.is_local_client():
            self.send_error(403, "Admin server is local-only")
            return
        if not self.is_same_origin():
            self.send_error(403, "Cross-origin requests are not allowed")
            return
        if not self.is_authorized():
            self.send_error(403, "Missing or invalid admin token")
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/catalog":
            self.save_catalog()
        elif parsed.path == "/api/git-push":
            self.git_push()
        else:
            self.send_error(404)

    def is_local_client(self) -> bool:
        return self.client_address[0] in LOCAL_HOSTS

    def is_same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return origin in {
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }

    def is_authorized(self) -> bool:
        return self.headers.get("X-Admin-Token") == self.server.admin_token

    def send_session(self):
        self.send_json_response(
            {
                "ok": True,
                "token": self.server.admin_token,
                "host": f"http://127.0.0.1:{self.server.server_port}",
            },
            no_store=True,
        )

    def send_catalog(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            self.send_json_response(data, no_store=True)
        except Exception as e:
            self.send_json_error(500, str(e))

    def save_catalog(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_length)
            data = json.loads(raw)

            # 백업
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = BACKUP_DIR / f"products_{timestamp}.yaml"
            shutil.copy2(CONFIG_PATH, backup_path)

            # 저장
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(
                    data,
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                    width=120,
                )

            body = json.dumps(
                {"ok": True, "backup": backup_path.name}, ensure_ascii=False
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        except Exception as e:
            self.send_json_error(500, str(e))

    def git_push(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_length)
            data = json.loads(raw) if raw else {}
            message = data.get("message", "제품 카탈로그 업데이트")

            # git add + commit + push
            run_git = lambda *args: subprocess.run(
                ["git", *args],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
            )

            # 변경사항 확인
            status = run_git("status", "--porcelain", "config/products.yaml")
            if not status.stdout.strip():
                self.send_json_response({"ok": True, "skipped": True, "detail": "변경사항 없음"})
                return

            add_result = run_git("add", "config/products.yaml")
            if add_result.returncode != 0:
                raise RuntimeError(f"git add failed: {add_result.stderr}")

            commit_result = run_git("commit", "-m", message)
            if commit_result.returncode != 0:
                raise RuntimeError(f"git commit failed: {commit_result.stderr}")

            push_result = run_git("push")
            if push_result.returncode != 0:
                raise RuntimeError(f"git push failed: {push_result.stderr}")

            self.send_json_response({"ok": True, "detail": "커밋 & 푸시 완료"})

        except Exception as e:
            self.send_json_error(500, str(e))

    def send_json_response(self, data, *, no_store=False):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json_error(self, code, message):
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    port = 8080
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    server = HTTPServer(("127.0.0.1", port), AdminHandler)
    server.admin_token = secrets.token_urlsafe(32)
    print(f"Admin server: http://127.0.0.1:{port}/admin.html")
    print(f"Config: {CONFIG_PATH}")
    print("Security: local-only + same-origin + session token required")
    print("Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
