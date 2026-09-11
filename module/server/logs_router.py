# This Python file uses the following encoding: utf-8
# Minimal-scrolls build: compatibility log-center router.
# AzurTian OASX expects a `/logs/...` log-center API (history window, live SSE,
# error-log browser). Upstream OAS did not ship these routes, so OASX kept
# receiving 404 on /logs/<name> and /logs/<name>/stream. This module implements
# exactly the JSON / SSE contract that OASX parses. It is read-only and does not
# touch any existing task logic.
import asyncio
import base64
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

logs_app = APIRouter(prefix="/logs")

# One display line is capped to this many raw bytes (OASX echoes it back).
MAX_LINE_BYTES = 8192
_SAFE_SCRIPT = re.compile(r"[^A-Za-z0-9_\-]")
_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9_\-]")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _log_dir() -> Path:
    return Path.cwd() / "log"


def _enc_cursor(offset: int) -> str:
    raw = str(int(offset)).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _dec_cursor(cursor: Optional[str], default: int = 0) -> int:
    if not cursor:
        return default
    try:
        token = cursor.strip()
        token += "=" * (-len(token) % 4)
        return int(base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8"))
    except Exception:
        return default


def _safe_name(name: str) -> bool:
    return bool(name) and not name.startswith(".") and "/" not in name and "\\" not in name and ".." not in name


def _bad_token(name: str) -> bool:
    # True when the token is empty or contains a character outside [A-Za-z0-9_-]
    return (not name) or bool(_SAFE_TOKEN.search(name))


def resolve_log_file(script_name: str) -> Optional[Path]:
    """Pick the active plain-text log for a config, newest first."""
    log_dir = _log_dir()
    if not log_dir.is_dir():
        return None
    script = _SAFE_SCRIPT.sub("", script_name or "")
    exact = sorted(
        log_dir.glob(f"*_{script}.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if exact:
        return exact[0]
    txts = [p for p in log_dir.glob("*.txt") if p.is_file()]
    if txts:
        return max(txts, key=lambda p: p.stat().st_mtime)
    return None


def split_lines(data: bytes, include_tail: bool = True) -> List[Tuple[int, bytes, int]]:
    """Return [(start_byte_offset, raw_bytes_no_newline, byte_length_with_nl)]."""
    out: List[Tuple[int, bytes, int]] = []
    start = 0
    for i, b in enumerate(data):
        if b == 0x0A:  # \n
            raw = data[start:i]
            out.append((start, raw, i + 1 - start))
            start = i + 1
    if include_tail and start < len(data):
        out.append((start, data[start:], len(data) - start))
    return out


def _make_line(path: Path, line_no: int, offset: int, raw: bytes, length: int) -> dict:
    body = raw[:-1] if raw.endswith(b"\r") else raw
    truncated = False
    if len(body) > MAX_LINE_BYTES:
        body = body[:MAX_LINE_BYTES]
        truncated = True
    return {
        "file_name": path.name,
        "line_no": line_no,
        "offset": offset,
        "byte_length": length,
        "text": body.decode("utf-8", errors="replace"),
        "line_truncated": truncated,
    }


def _position(path: Path, offset: int, line_no: int) -> dict:
    return {"file_name": path.name, "offset": offset, "line_no": line_no}


def _limits(limit_lines: int, limit_bytes: int) -> dict:
    return {
        "limit_lines": limit_lines,
        "limit_bytes": limit_bytes,
        "max_line_bytes": MAX_LINE_BYTES,
    }


def _empty_window(script_name: str, limit_lines: int, limit_bytes: int) -> dict:
    return {
        "script_name": script_name,
        "window": {},
        "older_cursor": None,
        "live_cursor": _enc_cursor(0),
        "has_older": False,
        "reached_start": True,
        "limits": _limits(limit_lines, limit_bytes),
        "lines": [],
    }


def _window_from_lines(
    path: Path,
    picked: List[Tuple[int, int, bytes, int]],
    total_size: int,
    total_lines: int,
    script_name: str,
    limit_lines: int,
    limit_bytes: int,
) -> dict:
    """picked: [(global_line_index, offset, raw, length),...] already old->new."""
    lines = [_make_line(path, idx + 1, off, raw, length) for idx, off, raw, length in picked]
    if not picked:
        return {
            "script_name": script_name,
            "window": {
                "from": _position(path, total_size, max(total_lines, 1)),
                "to": _position(path, total_size, max(total_lines, 1)),
            },
            "older_cursor": None,
            "live_cursor": _enc_cursor(total_size),
            "has_older": False,
            "reached_start": True,
            "limits": _limits(limit_lines, limit_bytes),
            "lines": [],
        }
    first_idx, first_off, _, _ = picked[0]
    last_idx, last_off, _, last_len = picked[-1]
    has_older = first_off > 0
    return {
        "script_name": script_name,
        "window": {
            "from": _position(path, first_off, first_idx + 1),
            "to": _position(path, last_off + last_len, last_idx + 1),
        },
        "older_cursor": _enc_cursor(first_off) if has_older else None,
        "live_cursor": _enc_cursor(total_size),
        "has_older": has_older,
        "reached_start": not has_older,
        "limits": _limits(limit_lines, limit_bytes),
        "lines": lines,
    }


def _read_tail_window(path: Path, script_name: str, limit_lines: int, limit_bytes: int) -> dict:
    data = path.read_bytes()
    all_lines = split_lines(data)
    picked: List[Tuple[int, int, bytes, int]] = []
    bytes_acc = 0
    for idx in range(len(all_lines) - 1, -1, -1):
        off, raw, length = all_lines[idx]
        if len(picked) >= limit_lines:
            break
        if picked and bytes_acc + length > limit_bytes:
            break
        picked.append((idx, off, raw, length))
        bytes_acc += length
    picked.reverse()
    return _window_from_lines(path, picked, len(data), len(all_lines), script_name, limit_lines, limit_bytes)


def _read_older_window(path: Path, anchor: int, script_name: str, limit_lines: int, limit_bytes: int) -> dict:
    data = path.read_bytes()
    all_lines = split_lines(data)
    # last line whose offset is strictly before the anchor
    end = -1
    for idx, (off, _, _) in enumerate(all_lines):
        if off < anchor:
            end = idx
        else:
            break
    picked: List[Tuple[int, int, bytes, int]] = []
    bytes_acc = 0
    for idx in range(end, -1, -1):
        off, raw, length = all_lines[idx]
        if len(picked) >= limit_lines:
            break
        if picked and bytes_acc + length > limit_bytes:
            break
        picked.append((idx, off, raw, length))
        bytes_acc += length
    picked.reverse()
    return _window_from_lines(path, picked, len(data), len(all_lines), script_name, limit_lines, limit_bytes)


# --------------------------------------------------------------------------- #
# error-log browser
# --------------------------------------------------------------------------- #
def _error_dir() -> Path:
    return _log_dir() / "error"


def _parse_error_item(child: Path) -> Optional[dict]:
    if not child.is_dir():
        return None
    name = child.name
    match = re.fullmatch(r"(\d+)(?:_([A-Za-z0-9\-]+))?", name)
    if match:
        ts = int(match.group(1))
        script = match.group(2)
        legacy = script is None
    else:
        ts = int(child.stat().st_mtime * 1000)
        script = None
        legacy = True
    log_file = child / "log.txt"
    images = sorted(child.glob("*.png"))
    return {
        "id": name,
        "directory": name,
        "script_name": script,
        "timestamp_ms": ts,
        "time": datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S"),
        "legacy": legacy,
        "log_size": log_file.stat().st_size if log_file.exists() else 0,
        "image_count": len(images),
        "_date": datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d"),
    }


def _list_error_items(date: Optional[str], script_name: Optional[str]) -> List[dict]:
    root = _error_dir()
    if not root.is_dir():
        return []
    items = []
    for child in root.iterdir():
        item = _parse_error_item(child)
        if item is None:
            continue
        if date and item["_date"] != date:
            continue
        if script_name and item["script_name"] != script_name:
            continue
        items.append(item)
    items.sort(key=lambda x: x["timestamp_ms"], reverse=True)
    return items


# --------------------------------------------------------------------------- #
# routes — literal /errors must be declared before /{script_name}
# --------------------------------------------------------------------------- #
@logs_app.get("/errors")
async def list_error_logs(
    limit: int = 100,
    date: Optional[str] = None,
    script_name: Optional[str] = None,
    cursor: Optional[str] = None,
):
    items = await run_in_threadpool(_list_error_items, date, script_name)
    start = _dec_cursor(cursor, 0)
    start = max(0, start)
    page = items[start:start + limit]
    for item in page:
        item.pop("_date", None)
    has_more = start + limit < len(items)
    next_cursor = _enc_cursor(start + limit) if has_more else None
    return {
        "date": date or "",
        "script_name": script_name,
        "items": page,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


@logs_app.get("/errors/{error_id}")
async def get_error_log(error_id: str, log_limit_bytes: int = 262144):
    if _bad_token(error_id):
        return {"id": error_id, "directory": error_id, "script_name": None,
                "timestamp_ms": 0, "time": "", "legacy": True,
                "log": {"file_name": "log.txt", "content": "", "size": 0,
                        "limit_bytes": log_limit_bytes, "truncated": False},
                "images": []}
    folder = _error_dir() / error_id
    item = _parse_error_item(folder) if folder.is_dir() else None
    if item is None:
        item = {"id": error_id, "directory": error_id, "script_name": None,
                "timestamp_ms": 0, "time": "", "legacy": True}
    item.pop("_date", None)
    log_file = folder / "log.txt"
    content, size, truncated = "", 0, False
    if log_file.is_file():
        raw = log_file.read_bytes()
        size = len(raw)
        truncated = size > log_limit_bytes
        shown = raw[-log_limit_bytes:] if truncated else raw
        content = shown.decode("utf-8", errors="replace")
    images = []
    if folder.is_dir():
        for img in sorted(folder.glob("*.png")):
            images.append({
                "name": img.name,
                "size": img.stat().st_size,
                "modified_time": datetime.fromtimestamp(img.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "url": f"/logs/errors/{error_id}/images/{img.name}",
            })
    item["log"] = {"file_name": "log.txt", "content": content, "size": size,
                   "limit_bytes": log_limit_bytes, "truncated": truncated}
    item["images"] = images
    return item


@logs_app.get("/errors/{error_id}/images/{image_name}")
async def get_error_image(error_id: str, image_name: str):
    if _bad_token(error_id) or not _safe_name(image_name):
        raise HTTPException(status_code=404, detail="image not found")
    path = (_error_dir() / error_id / image_name).resolve()
    if not str(path).startswith(str(_error_dir().resolve())) or not path.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(str(path), media_type="image/png")


@logs_app.get("/{script_name}")
async def get_log_window(
    script_name: str,
    limit_lines: int = 500,
    limit_bytes: int = 262144,
    cursor: Optional[str] = None,
):
    path = await run_in_threadpool(resolve_log_file, script_name)
    if path is None or not path.is_file():
        return _empty_window(script_name, limit_lines, limit_bytes)
    if cursor:
        anchor = _dec_cursor(cursor, 0)
        return await run_in_threadpool(
            _read_older_window, path, anchor, script_name, limit_lines, limit_bytes)
    return await run_in_threadpool(
        _read_tail_window, path, script_name, limit_lines, limit_bytes)


@logs_app.get("/{script_name}/stream")
async def stream_log(
    script_name: str,
    cursor: Optional[str] = None,
    limit_lines: int = 200,
    limit_bytes: int = 131072,
):
    async def event_source():
        def sse(event: str, payload: dict) -> bytes:
            text = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            return text.encode("utf-8")

        path = await run_in_threadpool(resolve_log_file, script_name)
        if cursor:
            offset = _dec_cursor(cursor, 0)
        else:
            offset = path.stat().st_size if path and path.is_file() else 0
        current_name = path.name if path else None
        pending = b""
        last_beat = time.time()
        yield sse("ready", {"cursor": _enc_cursor(offset)})

        while True:
            path = await run_in_threadpool(resolve_log_file, script_name)
            if path is not None and path.is_file():
                name = path.name
                size = path.stat().st_size
                # log rotation / new daily file / file rebuilt smaller
                if name != current_name or size < offset:
                    current_name = name
                    offset = 0
                    pending = b""
                    yield sse("rotate", {"cursor": _enc_cursor(0)})
                if size > offset:
                    with open(path, "rb") as fh:
                        fh.seek(offset)
                        chunk = fh.read(size - offset)
                    buffer = pending + chunk
                    complete = split_lines(buffer, include_tail=False)
                    if complete:
                        line_payloads = []
                        consumed = 0
                        for rel_off, raw, length in complete:
                            abs_off = offset + rel_off
                            # live lines dedup on file:offset; line_no uses abs offset to stay unique
                            line_payloads.append(_make_line(path, abs_off, abs_off, raw, length))
                            consumed = rel_off + length
                        pending = buffer[consumed:]
                        offset += consumed
                        last_beat = time.time()
                        yield sse("append", {"lines": line_payloads,
                                            "next_cursor": _enc_cursor(offset)})
                    else:
                        pending = buffer
            now = time.time()
            if now - last_beat >= 15:
                last_beat = now
                yield sse("heartbeat", {"cursor": _enc_cursor(offset)})
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
