import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import ipaddress
import json
from pathlib import Path
import secrets
import threading
import urllib.parse
import uuid
import zipfile

from .core import APP_ID, ROOT, VERSION, make_config, validate_config, parse_range


def make_server(application, port=0):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = "Puzzle71Local"

        def log_message(self, *_):
            pass

        def send(self, status, body, content_type="application/json; charset=utf-8", download=None):
            if isinstance(body, dict):
                body = json.dumps(body, ensure_ascii=False).encode("utf8")
            elif isinstance(body, str):
                body = body.encode("utf8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if download:
                self.send_header("Content-Disposition", 'attachment; filename="' + download + '"')
            self.end_headers()
            self.wfile.write(body)

        def local(self):
            expected = {"127.0.0.1:" + str(self.server.server_address[1]),
                        "localhost:" + str(self.server.server_address[1])}
            if self.headers.get("Host") not in expected or not ipaddress.ip_address(self.client_address[0]).is_loopback:
                raise PermissionError("本地控制接口拒绝非本机来源")
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in expected}:
                raise PermissionError("来源不匹配")

        def authenticated(self):
            self.local()
            if not hmac.compare_digest(self.headers.get("X-Local-Token", ""), token):
                raise PermissionError("本地会话已失效，请重新打开软件窗口")

        def do_GET(self):
            try:
                self.local()
                path = urllib.parse.urlparse(self.path).path
                if path == "/api/bootstrap":
                    self.send(200, {"token": token, "app": APP_ID, "state": application.snapshot()})
                elif path == "/api/ping":
                    self.send(200, {"app": APP_ID, "version": VERSION})
                elif path == "/api/state":
                    self.authenticated()
                    self.send(200, application.snapshot())
                elif path == "/api/claim/export":
                    self.authenticated()
                    if application.claim.snapshot()["state"] not in ("READY", "SUBMITTED"):
                        raise ValueError("没有可导出的已签名交易")
                    data = (application.directory / "private/signed-transaction.txt").read_bytes()
                    self.send(200, data, "text/plain", "signed-transaction.txt")
                else:
                    files = {"/": ("index.html", "text/html; charset=utf-8"),
                             "/style.css": ("style.css", "text/css; charset=utf-8"),
                             "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                             "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
                             "/icon.svg": ("icon.svg", "image/svg+xml")}
                    if path not in files:
                        self.send(404, {"error": "Not found"})
                        return
                    name, mime = files[path]
                    self.send(200, (ROOT / "web" / name).read_bytes(), mime)
            except PermissionError as error:
                self.send(403, {"error": str(error)})
            except (ValueError, OSError) as error:
                self.send(400, {"error": str(error)})

        def do_POST(self):
            try:
                self.authenticated()
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 16_384:
                    raise ValueError("请求过大")
                body = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Invalid request")
                path = urllib.parse.urlparse(self.path).path
                if path == "/api/start":
                    application.start(int(body.get("duration", 600)), body.get("backend", "auto"))
                elif path == "/api/stop":
                    application.stop()
                elif path == "/api/refresh":
                    application.monitor.refresh()
                elif path == "/api/preferences":
                    application.set_language(body.get("language"))
                elif path == "/api/payout":
                    application.set_payout(body.get("payout",""),body.get("auto",False),
                        body.get("confirmation",""),body.get("acknowledge_race_risk",False))
                elif path == "/api/claim/receipt":
                    application.claim.refresh_confirmation()
                elif path == "/api/config/import":
                    application.configure(validate_config(body))
                elif path == "/api/plan":
                    total = int(body.get("total", 1))
                    if not 1 <= total <= 1024:
                        raise ValueError("电脑数量为 1–1024")
                    plan_id = uuid.uuid4().hex
                    start,end=body.get("range_start"),body.get("range_end")
                    if start is not None or end is not None:
                        parse_range(start,end)
                    configs = [make_config(total, i, plan_id,start=start,end=end,
                        payout=application.store.config["payout"]) for i in range(1, total + 1)]
                    application.configure(configs[0])
                    output = io.BytesIO()
                    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                        for config in configs:
                            archive.writestr("computer-" + str(config["index"]).zfill(3) + ".json",
                                             json.dumps(config, indent=2, ensure_ascii=False))
                        archive.writestr("README.txt",
                            "同一套 Puzzle71Desk.exe 放到每台 Windows 10/11 x64 电脑。\n"
                            "每台导入不同编号的 computer-NNN.json。\n"
                            "不用公网端口，不用共享私钥；每台独立保存进度。\n"
                            "同一编号重复使用会重复计算；改变总台数前先停下旧计划。\n")
                    self.send(200, output.getvalue(), "application/zip", "puzzle71-computer-configs.zip")
                    return
                elif path == "/api/claim/prepare":
                    if not (application.directory / "private/puzzle71-result.dpapi").exists():
                        raise ValueError("本机尚未发现结果")
                    threading.Thread(target=application.claim.prepare, daemon=True).start()
                elif path == "/api/claim/broadcast":
                    result = application.claim.confirm_broadcast(body.get("nonce"), body.get("confirmation"),
                                                                body.get("acknowledge_race_risk"))
                    self.send(200, result)
                    return
                elif path == "/api/quit":
                    application.stop("用户退出软件")
                    self.send(200, {"ok": True})
                    threading.Thread(target=lambda: (application.shutdown(), self.server.shutdown()), daemon=True).start()
                    return
                else:
                    self.send(404, {"error": "Not found"})
                    return
                self.send(200, {"ok": True})
            except PermissionError as error:
                self.send(403, {"error": str(error)})
            except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
                self.send(400, {"error": str(error)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
