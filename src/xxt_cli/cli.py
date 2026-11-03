from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
from . import __version__, parsers
from .browser import Browser
from .session import CliError, Session, atomic_write


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError("INVALID_ARGUMENT", message, 2)


def positive(value):
    try:
        n = int(value)
        if n > 0:
            return n
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("需为正整数")


def parser():
    p = Parser(description="学习通作业抓取 CLI；结果输出 JSON，诊断输出 stderr。")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--state-dir", type=Path, default=Path(os.environ.get("XXT_STATE_DIR", "~/.local/share/xxt-agent")))
    p.add_argument("--headed", action="store_true")
    p.add_argument("--timeout", type=positive, default=30000, help="页面等待时间，毫秒")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("schema", help="输出命令、结果字段和退出码协议")
    sub.add_parser("doctor", help="检查依赖与 Chromium 安装")
    auth = sub.add_parser("auth").add_subparsers(dest="auth_command", required=True)
    login = auth.add_parser("login", help="打开浏览器供用户扫码登录")
    login.add_argument("--wait", type=positive, default=180, help="登录等待秒数")
    imp = auth.add_parser("import", help="从本地 JSON 或原客户端登录态导入")
    imp.add_argument("file", type=Path)
    imp.add_argument("--legacy-key", type=Path)
    auth.add_parser("status", help="检查本地登录态，在线有效性由 courses 检查")
    sub.add_parser("courses")
    hw = sub.add_parser("homeworks")
    hw.add_argument("--course-url", required=True)
    hw.add_argument("--max-pages", type=positive, default=100)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--max-pages", type=positive, default=100)
    fetch.add_argument("--output", type=Path, help="额外写入完整 JSON")
    fetch.add_argument("--markdown", type=Path)
    sync = sub.add_parser("sync", help="按课程批量抓取并支持断点续传")
    sync.add_argument("--course-url", required=True)
    sync.add_argument("--output-dir", type=Path, required=True)
    sync.add_argument("--max-pages", type=positive, default=100)
    sync.add_argument("--refresh", action="store_true", help="重新抓取已有结果")
    offline = sub.add_parser("parse", help="离线解析已保存 HTML")
    offline.add_argument("file", type=Path)
    offline.add_argument("--kind", choices=("courses", "homeworks", "questions"), default="questions")
    offline.add_argument("--base-url", default="https://mooc1.chaoxing.com/")
    return p


def schema():
    return {"name": "xxt", "version": __version__, "schema_version": 1,
            "commands": ["schema", "doctor", "auth login", "auth import", "auth status", "courses", "homeworks", "fetch", "sync", "parse"],
            "envelope": {"ok": "boolean", "schema_version": 1, "data": "object on success", "error": {"code": "string", "message": "string", "details": "optional object"}},
            "exit_codes": {"0": "success", "1": "runtime error", "2": "invalid argument", "3": "authentication or verification required", "4": "page extraction or incomplete pagination", "5": "partial batch failure", "130": "interrupted"},
            "question_fields": ["id", "index", "type", "title", "options", "correct_answer", "student_answer", "analysis", "attachments"],
            "notes": ["全局参数置于子命令前", "stdout 每次输出一个 JSON 对象；--help 和 --version 输出文本", "图片与附件以 URL 保留", "时间提示保留原始 time_text", "sync 默认重新抓取未交作业；完整已完成作业按 ID 续传；--refresh 强制刷新"]}


def run(args):
    session = Session(args.state_dir)
    if args.command == "schema":
        return schema()
    if args.command == "doctor":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            ready = Path(pw.chromium.executable_path).exists()
        return {"python": sys.version.split()[0], "chromium_installed": ready,
                "install_command": "python -m playwright install chromium", "version": __version__}
    if args.command == "parse":
        data = getattr(parsers, args.kind)(args.file.read_text(encoding="utf-8"), args.base_url)
        return data if isinstance(data, dict) else {"items": data, "count": len(data)}
    if args.command == "auth":
        if args.auth_command == "import":
            session.import_file(args.file, args.legacy_key)
            return {"imported": True}
        if args.auth_command == "status":
            session.load()
            return {"session_available": True, "online_verified": False}
        with Browser(session, headed=True, timeout=args.timeout, login=True) as b:
            b.goto("https://passport2.chaoxing.com/login")
            print("请在打开的浏览器中扫码或登录。", file=sys.stderr)
            deadline = time.monotonic() + args.wait
            while time.monotonic() < deadline:
                cookies = b.context.cookies()
                if any(c["name"] == "_uid" and c["value"] for c in cookies) and "passport" not in b.page.url:
                    # Validate against course discovery before persisting.
                    b.courses()
                    session.save(b.context.storage_state())
                    return {"authenticated": True}
                b.page.wait_for_timeout(300)
        raise CliError("LOGIN_TIMEOUT", "扫码登录等待超时。", 3)
    with Browser(session, headed=args.headed, timeout=args.timeout) as b:
        if args.command == "courses":
            return b.courses()
        if args.command == "homeworks":
            data = b.homework_list(args.course_url, args.max_pages)
            if not data["complete"]:
                raise CliError("INCOMPLETE", "作业列表分页尚未完整。", 4, data)
            return data
        if args.command == "fetch":
            data = b.fetch(args.url, args.max_pages)
            if args.output:
                atomic_write(args.output, json.dumps(data, ensure_ascii=False, indent=2), private=True)
            if args.markdown:
                atomic_write(args.markdown, parsers.markdown(data), private=True)
            if not data["complete"]:
                raise CliError("INCOMPLETE", "题目抓取尚未完整。", 4, data)
            return data
        listing = b.homework_list(args.course_url, args.max_pages)
        if not listing["complete"]:
            raise CliError("INCOMPLETE", "作业列表分页尚未完整。", 4, listing)
        results, failed = [], []
        for item in listing["items"]:
            path = args.output_dir / (item["id"] + ".json")
            try:
                try:
                    old = json.loads(path.read_text()) if path.exists() and not args.refresh else {}
                    if not isinstance(old, dict):
                        old = {}
                except (OSError, ValueError):
                    old = {}
                reusable = old.get("id") == item["id"] and old.get("complete") and item["status"] in ("已完成", "已批阅") and old.get("source_status") == item["status"]
                if reusable:
                    results.append({"id": item["id"], "status": "cached", "path": str(path.resolve())})
                    continue
                print("抓取 " + item["id"], file=sys.stderr)
                data = b.fetch(item["url"], args.max_pages)
                data["source_status"] = item["status"]
                if not data["complete"]:
                    raise CliError("INCOMPLETE", "题目抓取尚未完整。", 4)
                atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2), private=True)
                atomic_write(path.with_suffix(".md"), parsers.markdown(data), private=True)
                results.append({"id": item["id"], "status": "fetched", "path": str(path.resolve())})
            except Exception as e:
                failed.append({"id": item["id"], "code": e.code if isinstance(e, CliError) else "FETCH_FAILED"})
        summary = {"items": results, "failed": failed, "count": len(results)}
        atomic_write(args.output_dir / "manifest.json", json.dumps(summary, ensure_ascii=False, indent=2), private=True)
        if failed:
            raise CliError("PARTIAL_FAILURE", "部分作业抓取失败，重跑命令可继续。", 5, summary)
        return summary


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        data = run(args)
        result, code = {"ok": True, "schema_version": 1, "data": data}, 0
    except CliError as e:
        error = {"code": e.code, "message": str(e)}
        if e.details is not None:
            error["details"] = e.details
        result, code = {"ok": False, "schema_version": 1, "error": error}, e.exit_code
    except KeyboardInterrupt:
        result, code = {"ok": False, "schema_version": 1, "error": {"code": "INTERRUPTED", "message": "操作已中断。"}}, 130
    except Exception as e:
        # Exception strings from browser/network libraries may contain signed URLs.
        print("诊断类型：" + type(e).__name__, file=sys.stderr)
        result, code = {"ok": False, "schema_version": 1, "error": {"code": "RUNTIME_ERROR", "message": "操作失败；请检查依赖、文件路径和浏览器安装。"}}, 1
    print(json.dumps(result, ensure_ascii=False))
    return code
