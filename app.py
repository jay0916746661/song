#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / "downloads"
SOURCES_FILE = ROOT / "sources.json"
CANDIDATES_FILE = ROOT / "candidates.json"
LOCAL_PYTHON = ROOT / ".venv" / "bin" / "python"
PYTHON = os.environ.get("PYTHON_BIN") or str(LOCAL_PYTHON if LOCAL_PYTHON.exists() else Path(sys.executable))
HOST = os.environ.get("HOST", "0.0.0.0" if os.environ.get("RENDER") else "127.0.0.1")
PORT = int(os.environ.get("PORT", "8787"))

JOBS = {}
SCAN_JOBS = {}
JOBS_LOCK = threading.Lock()
URL_RE = re.compile(r"^https?://", re.IGNORECASE)
SUPPORTED_HINTS = ("instagram.com", "facebook.com", "fb.watch", "youtube.com", "youtu.be")
OUTPUT_FORMATS = {"video", "mp3", "wav"}
FFMPEG_PATH = None


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def public_job(job):
    result = dict(job)
    result.pop("process", None)
    return result


def load_json(path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return fallback


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_sources():
    data = load_json(SOURCES_FILE, {"sources": []})
    return data.get("sources", [])


def save_sources(sources):
    save_json(SOURCES_FILE, {"sources": sources})


def load_candidates():
    data = load_json(CANDIDATES_FILE, {"candidates": []})
    return data.get("candidates", [])


def save_candidates(candidates):
    save_json(CANDIDATES_FILE, {"candidates": candidates})


def detect_platform(url):
    host = urlparse(url).netloc.lower()
    if "instagram.com" in host:
        return "Instagram"
    if "facebook.com" in host or "fb.watch" in host:
        return "Facebook"
    if "youtube.com" in host or "youtu.be" in host:
        return "YouTube"
    return "其他"


def safe_download_path(name):
    candidate = (DOWNLOADS / name).resolve()
    if DOWNLOADS.resolve() not in candidate.parents and candidate != DOWNLOADS.resolve():
        raise ValueError("Invalid file path")
    return candidate


def list_downloads():
    DOWNLOADS.mkdir(exist_ok=True)
    files = []
    for path in sorted(DOWNLOADS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file() or path.name.startswith("."):
            continue
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                "url": "/downloads/" + path.name,
            }
        )
    return files


def normalize_output_format(value):
    output_format = (value or "video").strip().lower()
    if output_format not in OUTPUT_FORMATS:
        return "video"
    return output_format


def get_ffmpeg_path():
    global FFMPEG_PATH
    if FFMPEG_PATH:
        return FFMPEG_PATH
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        FFMPEG_PATH = system_ffmpeg
        return FFMPEG_PATH
    try:
        process = subprocess.run(
            [
                str(PYTHON),
                "-c",
                "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())",
            ],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
        candidate = process.stdout.strip()
        if process.returncode == 0 and candidate and Path(candidate).exists():
            FFMPEG_PATH = candidate
            return FFMPEG_PATH
    except Exception:
        return None
    return None


def normalize_candidate_url(info, source_url):
    for key in ("webpage_url", "original_url", "url"):
        value = info.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    entry_id = info.get("id")
    source_host = urlparse(source_url).netloc.lower()
    if entry_id and ("youtube.com" in source_host or "youtu.be" in source_host):
        return f"https://www.youtube.com/watch?v={entry_id}"
    return source_url


def run_scan(scan_id):
    sources = load_sources()
    collected = []
    lines = []
    seen = set()

    with JOBS_LOCK:
        SCAN_JOBS[scan_id]["status"] = "running"
        SCAN_JOBS[scan_id]["message"] = f"正在掃描 {len(sources)} 個來源..."

    if not sources:
        with JOBS_LOCK:
            SCAN_JOBS[scan_id]["status"] = "done"
            SCAN_JOBS[scan_id]["message"] = "還沒有喜好來源。"
            SCAN_JOBS[scan_id]["finished_at"] = now()
        return

    for source in sources:
        source_url = source["url"]
        label = source.get("label") or source_url
        lines.append(f"[source] {label}")
        command = [
            str(PYTHON),
            "-m",
            "yt_dlp",
            "--flat-playlist",
            "--dump-json",
            "--playlist-end",
            "12",
            source_url,
        ]
        try:
            process = subprocess.run(
                command,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=90,
            )
            output_lines = [line.strip() for line in process.stdout.splitlines() if line.strip()]
            if process.returncode != 0:
                lines.append(f"[skip] {label}: 掃描失敗，可能需要登入或不是公開來源。")
                lines.extend(output_lines[-5:])
            for raw in output_lines:
                if not raw.startswith("{"):
                    continue
                try:
                    info = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                candidate_url = normalize_candidate_url(info, source_url)
                key = candidate_url or info.get("id")
                if not key or key in seen:
                    continue
                seen.add(key)
                title = info.get("title") or info.get("fulltitle") or "未命名影片"
                collected.append(
                    {
                        "id": uuid.uuid4().hex[:10],
                        "title": title,
                        "url": candidate_url,
                        "platform": detect_platform(candidate_url or source_url),
                        "source": label,
                        "source_url": source_url,
                        "duration": info.get("duration_string") or info.get("duration"),
                        "added_at": now(),
                    }
                )
                lines.append(f"[found] {title}")
        except subprocess.TimeoutExpired:
            lines.append(f"[skip] {label}: 掃描逾時。")
        except Exception as exc:
            lines.append(f"[skip] {label}: {exc}")

        with JOBS_LOCK:
            SCAN_JOBS[scan_id]["log"] = lines[-160:]
            SCAN_JOBS[scan_id]["message"] = lines[-1] if lines else "掃描中..."

    existing = load_candidates()
    existing_by_url = {item.get("url"): item for item in existing if item.get("url")}
    for item in reversed(collected):
        existing_by_url[item["url"]] = item
    merged = sorted(existing_by_url.values(), key=lambda item: item.get("added_at", ""), reverse=True)[:120]
    save_candidates(merged)

    with JOBS_LOCK:
        SCAN_JOBS[scan_id]["status"] = "done"
        SCAN_JOBS[scan_id]["message"] = f"掃描完成，找到 {len(collected)} 個候選影片。"
        SCAN_JOBS[scan_id]["log"] = lines[-160:]
        SCAN_JOBS[scan_id]["candidates"] = merged
        SCAN_JOBS[scan_id]["finished_at"] = now()


def build_download_command(url, output_format):
    ffmpeg_path = get_ffmpeg_path()
    base = [
        str(PYTHON),
        "-m",
        "yt_dlp",
        "--newline",
        "--no-playlist",
        "--restrict-filenames",
        "--trim-filenames",
        "120",
    ]
    if ffmpeg_path:
        base.extend(["--ffmpeg-location", ffmpeg_path])
    if output_format == "video":
        return base + [
            "-o",
            str(DOWNLOADS / "%(extractor)s_%(id)s_%(title).80B.%(ext)s"),
            url,
        ]
    return base + [
        "--extract-audio",
        "--audio-format",
        output_format,
        "--audio-quality",
        "0",
        "-o",
        str(DOWNLOADS / "%(extractor)s_%(id)s_%(title).80B.%(ext)s"),
        url,
    ]


def run_download(job_id, url, output_format):
    DOWNLOADS.mkdir(exist_ok=True)
    if output_format in {"mp3", "wav"} and not get_ffmpeg_path():
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["message"] = "MP3/WAV 轉檔需要 ffmpeg。請先安裝相依套件，或改選影片檔。"
            JOBS[job_id]["log"] = ["找不到 ffmpeg 或 imageio-ffmpeg，因此無法抽取音訊或轉成 MP3/WAV。"]
            JOBS[job_id]["finished_at"] = now()
        return

    command = build_download_command(url, output_format)

    with JOBS_LOCK:
        JOBS[job_id]["status"] = "running"
        JOBS[job_id]["message"] = f"正在下載 {output_format.upper() if output_format != 'video' else '影片'}..."
        JOBS[job_id]["command"] = " ".join(command)

    try:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with JOBS_LOCK:
            JOBS[job_id]["process"] = process

        lines = []
        assert process.stdout is not None
        for line in process.stdout:
            clean = line.strip()
            if not clean:
                continue
            lines.append(clean)
            if len(lines) > 120:
                lines = lines[-120:]
            with JOBS_LOCK:
                JOBS[job_id]["log"] = lines
                JOBS[job_id]["message"] = clean

        code = process.wait()
        with JOBS_LOCK:
            JOBS[job_id]["finished_at"] = now()
            JOBS[job_id]["files"] = list_downloads()
            if code == 0:
                JOBS[job_id]["status"] = "done"
                JOBS[job_id]["message"] = "下載完成"
            else:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["message"] = "下載失敗，可能需要登入、影片不是公開，或平台限制下載。"
    except Exception as exc:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["message"] = str(exc)
            JOBS[job_id]["finished_at"] = now()


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed = urlparse(path)
        if parsed.path == "/":
            return str(ROOT / "index.html")
        if parsed.path.startswith("/downloads/"):
            name = unquote(parsed.path.removeprefix("/downloads/"))
            return str(safe_download_path(name))
        return str(ROOT / parsed.path.lstrip("/"))

    def send_json(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/jobs":
            with JOBS_LOCK:
                jobs = [public_job(job) for job in JOBS.values()]
            jobs.sort(key=lambda item: item["created_at"], reverse=True)
            return self.send_json({"jobs": jobs})
        if parsed.path == "/api/downloads":
            return self.send_json({"files": list_downloads()})
        if parsed.path == "/api/sources":
            return self.send_json({"sources": load_sources()})
        if parsed.path == "/api/candidates":
            return self.send_json({"candidates": load_candidates()})
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    return self.send_json({"error": "找不到任務"}, HTTPStatus.NOT_FOUND)
                return self.send_json({"job": public_job(job)})
        if parsed.path.startswith("/api/scans/"):
            scan_id = parsed.path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = SCAN_JOBS.get(scan_id)
                if not job:
                    return self.send_json({"error": "找不到掃描任務"}, HTTPStatus.NOT_FOUND)
                return self.send_json({"scan": public_job(job)})
        return super().do_GET()

    def do_POST(self):
        try:
            if self.path == "/api/sources":
                payload = self.read_json()
                url = (payload.get("url") or "").strip()
                label = (payload.get("label") or "").strip()
                if not URL_RE.match(url):
                    return self.send_json({"error": "請貼上 http 或 https 來源連結"}, HTTPStatus.BAD_REQUEST)
                sources = load_sources()
                if any(item["url"] == url for item in sources):
                    return self.send_json({"error": "這個來源已經存在"}, HTTPStatus.BAD_REQUEST)
                source = {
                    "id": uuid.uuid4().hex[:10],
                    "label": label or detect_platform(url),
                    "url": url,
                    "platform": detect_platform(url),
                    "created_at": now(),
                }
                sources.insert(0, source)
                save_sources(sources)
                return self.send_json({"source": source, "sources": sources}, HTTPStatus.CREATED)

            if self.path == "/api/sources/remove":
                payload = self.read_json()
                source_id = payload.get("id")
                sources = [item for item in load_sources() if item.get("id") != source_id]
                save_sources(sources)
                return self.send_json({"sources": sources})

            if self.path == "/api/scan":
                scan_id = uuid.uuid4().hex[:12]
                scan = {
                    "id": scan_id,
                    "status": "queued",
                    "message": "等待掃描...",
                    "log": [],
                    "candidates": load_candidates(),
                    "created_at": now(),
                    "finished_at": None,
                }
                with JOBS_LOCK:
                    SCAN_JOBS[scan_id] = scan
                thread = threading.Thread(target=run_scan, args=(scan_id,), daemon=True)
                thread.start()
                return self.send_json({"scan": public_job(scan)}, HTTPStatus.ACCEPTED)

            if self.path == "/api/candidates/clear":
                save_candidates([])
                return self.send_json({"candidates": []})

            if self.path != "/api/download":
                return self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

            payload = self.read_json()
            url = (payload.get("url") or "").strip()
            output_format = normalize_output_format(payload.get("format"))
            if not URL_RE.match(url):
                return self.send_json({"error": "請貼上 http 或 https 影片連結"}, HTTPStatus.BAD_REQUEST)
            host = urlparse(url).netloc.lower()
            hint = "支援 YouTube、Instagram、Facebook；其他 yt-dlp 可處理的公開連結也可以試。"
            if not any(domain in host for domain in SUPPORTED_HINTS):
                hint = "這看起來不是 FB / IG / YouTube，但我會用 yt-dlp 嘗試下載。"

            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "url": url,
                "format": output_format,
                "status": "queued",
                "message": hint,
                "log": [],
                "files": [],
                "created_at": now(),
                "finished_at": None,
            }
            with JOBS_LOCK:
                JOBS[job_id] = job
            thread = threading.Thread(target=run_download, args=(job_id, url, output_format), daemon=True)
            thread.start()
            return self.send_json({"job": public_job(job)}, HTTPStatus.ACCEPTED)
        except json.JSONDecodeError:
            return self.send_json({"error": "資料格式錯誤"}, HTTPStatus.BAD_REQUEST)


def main():
    DOWNLOADS.mkdir(exist_ok=True)
    if not Path(PYTHON).exists():
        raise SystemExit(f"Missing yt-dlp environment: {PYTHON}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Video panel running at http://{HOST}:{PORT}")
    print(f"Downloads folder: {DOWNLOADS}")
    server.serve_forever()


if __name__ == "__main__":
    main()
