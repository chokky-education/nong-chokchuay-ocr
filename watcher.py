import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import ocr_service
import storage_service
from config import AppConfig
from storage_service import VaultStorageCoordinator

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".pdf"}
IGNORED_SUFFIXES = (".tmp", ".part", ".crdownload", ".download", ".opid")


def _map_subfolder_to_tags(subfolder_name: str) -> List[str]:
    """Map an inbox subdirectory name to default owner/context tags."""
    cleaned = (subfolder_name or "").strip()
    if not cleaned:
        return []
    lowered = cleaned.lower()
    if lowered in {"teacher", "kru", "ครู", "ครูอนุบาล", "kindergarten"}:
        return ["ครูอนุบาล"]
    if lowered in {"me", "personal", "ส่วนตัว", "ฉัน"}:
        return ["ส่วนตัว"]
    return [cleaned]


class InboxWatcher:
    """Watches the local inbox folder and ingests completed files into the Vault."""

    def __init__(
        self,
        config: AppConfig,
        engine_getter: Callable[[], Any],
        storage: Optional[VaultStorageCoordinator] = None,
    ) -> None:
        self.config = config
        self.engine_getter = engine_getter
        self.storage = storage
        self.is_scanning: bool = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pending_sizes: Dict[str, int] = {}
        self._active_op_ids: Dict[str, Tuple[int, str, str]] = {}
        self.last_scan_at: str = ""
        self.last_result: Dict[str, Any] = {"processed": 0, "failed": 0, "skipped": 0}

    def _is_candidate_file(self, name: str, full_path: str) -> bool:
        if not os.path.isfile(full_path):
            return False
        if name.startswith("."):
            return False
        lower = name.lower()
        if lower.endswith(IGNORED_SUFFIXES):
            return False
        _, ext = os.path.splitext(lower)
        return ext in SUPPORTED_EXTENSIONS

    def _opid_sidecar_path(self, full_path: str) -> str:
        return f"{full_path}.opid"

    def _remove_opid_sidecar(self, full_path: str) -> None:
        self._active_op_ids.pop(full_path, None)
        sidecar = self._opid_sidecar_path(full_path)
        if os.path.exists(sidecar):
            try:
                os.remove(sidecar)
            except OSError:
                pass

    @staticmethod
    def _parse_opid_sidecar(raw: str) -> Optional[Tuple[int, str, str]]:
        """Validate and parse '<size>:<sha256>:<op_id>' sidecar content."""
        match = re.fullmatch(
            r"(\d+):([0-9a-f]{64}):(watcher:[^\r\n\t\x00]+:(\d+):([0-9a-f]{64}):[0-9a-f]{12})",
            raw,
        )
        if not match:
            return None
        sidecar_size = int(match.group(1))
        sidecar_sha256 = match.group(2)
        op_id = match.group(3)
        embedded_size = int(match.group(4))
        embedded_sha256 = match.group(5)
        if sidecar_size != embedded_size or sidecar_sha256 != embedded_sha256:
            return None
        if any(ord(ch) < 32 for ch in op_id):
            return None
        return (sidecar_size, sidecar_sha256, op_id)

    def _get_or_create_operation_id(
        self,
        full_path: str,
        rel_path: str,
        file_size: int,
        file_sha256: str,
    ) -> str:
        sidecar = self._opid_sidecar_path(full_path)
        prefix = f"{file_size}:{file_sha256}:"
        if os.path.lexists(sidecar):
            if not os.path.isfile(sidecar):
                raise storage_service.StorageUnavailableError(
                    f"Operation ID sidecar is not a regular file: '{sidecar}'",
                    error_code="watcher_opid_io_error",
                )
            try:
                with open(sidecar, "r", encoding="utf-8") as fh:
                    raw = fh.read().strip("\r\n")
            except UnicodeDecodeError as exc:
                raise storage_service.StorageUnavailableError(
                    f"Corrupted operation ID sidecar '{sidecar}': {exc}",
                    error_code="watcher_opid_corrupted",
                ) from exc
            except OSError as exc:
                raise storage_service.StorageUnavailableError(
                    f"Failed to read operation ID sidecar '{sidecar}': {exc}",
                    error_code="watcher_opid_io_error",
                ) from exc

            parsed = self._parse_opid_sidecar(raw)
            if parsed is None:
                raise storage_service.StorageUnavailableError(
                    f"Corrupted operation ID sidecar '{sidecar}'",
                    error_code="watcher_opid_corrupted",
                )
            parsed_size, parsed_sha256, existing_op_id = parsed
            if parsed_size == file_size and parsed_sha256 == file_sha256:
                self._active_op_ids[full_path] = (file_size, file_sha256, existing_op_id)
                return existing_op_id

        cached = self._active_op_ids.get(full_path)
        if cached and cached[0] == file_size and cached[1] == file_sha256:
            op_id = cached[2]
        else:
            path_hash = hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:16]
            op_id = f"watcher:{path_hash}:{file_size}:{file_sha256}:{uuid.uuid4().hex[:12]}"
            self._active_op_ids[full_path] = (file_size, file_sha256, op_id)

        expected_payload = f"{prefix}{op_id}"
        tmp_sidecar = f"{sidecar}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            with open(tmp_sidecar, "w", encoding="utf-8") as fh:
                fh.write(f"{expected_payload}\n")
                fh.flush()
                if hasattr(fh, "fileno"):
                    os.fsync(fh.fileno())
            os.replace(tmp_sidecar, sidecar)
            with open(sidecar, "r", encoding="utf-8") as fh:
                persisted = fh.read().strip("\r\n")
        except OSError as exc:
            if os.path.exists(tmp_sidecar):
                try:
                    os.remove(tmp_sidecar)
                except OSError:
                    pass
            raise storage_service.StorageUnavailableError(
                f"Failed to persist operation ID sidecar '{sidecar}': {exc}",
                error_code="watcher_opid_io_error",
            ) from exc

        if persisted != expected_payload:
            raise storage_service.StorageUnavailableError(
                f"Operation ID sidecar verification mismatch for '{sidecar}'",
                error_code="watcher_opid_corrupted",
            )
        return op_id

    def _cleanup_orphan_opids(self) -> None:
        if not os.path.isdir(self.config.inbox_dir):
            return
        for root_dir in [self.config.inbox_dir] + [
            os.path.join(self.config.inbox_dir, d)
            for d in os.listdir(self.config.inbox_dir)
            if not d.startswith(".") and os.path.isdir(os.path.join(self.config.inbox_dir, d))
        ]:
            for entry in os.listdir(root_dir):
                if entry.lower().endswith(".opid"):
                    sidecar_path = os.path.join(root_dir, entry)
                    parent_path = sidecar_path[:-5]
                    if not os.path.exists(parent_path):
                        try:
                            os.remove(sidecar_path)
                        except OSError:
                            pass

    def _quarantine_file(self, file_path: str, filename: str, error_msg: str) -> None:
        os.makedirs(self.config.inbox_failed_dir, exist_ok=True)
        self._remove_opid_sidecar(file_path)
        dest_path = os.path.join(self.config.inbox_failed_dir, filename)
        if os.path.exists(dest_path):
            stem, ext = os.path.splitext(filename)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest_path = os.path.join(self.config.inbox_failed_dir, f"{stem}_{stamp}{ext}")

        shutil.move(file_path, dest_path)
        err_note_path = f"{dest_path}.error.txt"
        with open(err_note_path, "w", encoding="utf-8") as fh:
            fh.write(f"Failed at: {datetime.now().isoformat()}\nError: {error_msg}\n")

    def _iter_candidate_files(self) -> List[Tuple[str, str, List[str]]]:
        """Collect (filename, full_path, auto_tags) across inbox root and first-level subfolders."""
        candidates: List[Tuple[str, str, List[str]]] = []
        if not os.path.isdir(self.config.inbox_dir):
            return candidates

        for name in sorted(os.listdir(self.config.inbox_dir)):
            full_path = os.path.join(self.config.inbox_dir, name)
            if self._is_candidate_file(name, full_path):
                candidates.append((name, full_path, []))
            elif not name.startswith(".") and os.path.isdir(full_path):
                auto_tags = _map_subfolder_to_tags(name)
                for sub_name in sorted(os.listdir(full_path)):
                    sub_full_path = os.path.join(full_path, sub_name)
                    if self._is_candidate_file(sub_name, sub_full_path):
                        candidates.append((sub_name, sub_full_path, auto_tags))
        return candidates

    def scan_once(self, require_stability: bool = False) -> Dict[str, Any]:
        """Scan inbox once, ingesting valid files and quarantining non-storage failures."""
        with self._lock:
            if self.storage is not None and self.storage.state != "READY":
                return {
                    "processed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "doc_ids": [],
                    "processed_ids": [],
                    "failed_files": [],
                    "storage_state": self.storage.state,
                }

            self.is_scanning = True
            try:
                self.config.ensure_dirs()
                self._cleanup_orphan_opids()
                processed_ids: List[str] = []
                failed_files: List[str] = []
                skipped = 0

                candidates = self._iter_candidate_files()
                active_paths = set()

                for name, full_path, auto_tags in candidates:
                    active_paths.add(full_path)

                    try:
                        size = os.path.getsize(full_path)
                    except OSError:
                        skipped += 1
                        continue

                    if require_stability:
                        prev_size = self._pending_sizes.get(full_path)
                        if prev_size is None or prev_size != size:
                            self._pending_sizes[full_path] = size
                            skipped += 1
                            continue

                    self._pending_sizes.pop(full_path, None)
                    try:
                        with open(full_path, "rb") as fh:
                            contents = fh.read()
                        file_sha256 = hashlib.sha256(contents).hexdigest()
                        rel_path = os.path.relpath(full_path, self.config.inbox_dir)
                        op_id = self._get_or_create_operation_id(
                            full_path,
                            rel_path,
                            len(contents),
                            file_sha256,
                        )

                        engine = self.engine_getter()
                        res = ocr_service.ingest_document(
                            contents=contents,
                            filename=name,
                            engine=engine,
                            config=self.config,
                            process_all_pages=True,
                            save_to_db=True,
                            tags=auto_tags,
                            ingestion_channel="watch_folder",
                            storage=self.storage,
                            operation_id=op_id,
                        )
                        os.remove(full_path)
                        self._remove_opid_sidecar(full_path)
                        if res.get("doc_id"):
                            processed_ids.append(res["doc_id"])
                    except storage_service.StorageError as exc:
                        print(f"[Watcher] Deferred '{name}' due to storage error ({exc.error_code}): {exc}")
                        skipped += 1
                    except Exception as exc:
                        self._quarantine_file(full_path, name, str(exc))
                        failed_files.append(name)

                # Drop stale entries for removed files
                for tracked in list(self._pending_sizes.keys()):
                    if tracked not in active_paths:
                        self._pending_sizes.pop(tracked, None)
                for tracked in list(self._active_op_ids.keys()):
                    if tracked not in active_paths:
                        self._active_op_ids.pop(tracked, None)

                self.last_scan_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                summary = {
                    "processed": len(processed_ids),
                    "failed": len(failed_files),
                    "skipped": skipped,
                    "doc_ids": processed_ids,
                    "failed_files": failed_files,
                    "scanned_at": self.last_scan_at,
                }
                self.last_result = summary
                return summary
            finally:
                self.is_scanning = False

    def get_status(self) -> Dict[str, Any]:
        self.config.ensure_dirs()
        pending = [name for name, _, _ in self._iter_candidate_files()]
        failed = [
            name
            for name in os.listdir(self.config.inbox_failed_dir)
            if os.path.isfile(os.path.join(self.config.inbox_failed_dir, name))
            and not name.endswith(".error.txt")
            and not name.lower().endswith(".opid")
        ]
        return {
            "enabled": self.config.inbox_poll_seconds > 0,
            "running": bool(self._thread and self._thread.is_alive()),
            "is_scanning": self.is_scanning,
            "polling_seconds": self.config.inbox_poll_seconds,
            "inbox_dir": self.config.inbox_dir,
            "inbox_subfolders": {
                "teacher": os.path.join(self.config.inbox_dir, "teacher"),
                "me": os.path.join(self.config.inbox_dir, "me"),
            },
            "inbox_failed_dir": self.config.inbox_failed_dir,
            "pending_count": len(pending),
            "failed_count": len(failed),
            "last_scan_at": self.last_scan_at,
            "last_result": self.last_result,
        }

    def _run_loop(self) -> None:
        while not self._stop_event.wait(timeout=max(0.2, self.config.inbox_poll_seconds)):
            try:
                self.scan_once(require_stability=True)
            except Exception as exc:
                print("[Watcher] Poll error:", exc)

    def start(self) -> None:
        if self.config.inbox_poll_seconds <= 0:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="inbox-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
