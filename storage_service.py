import hashlib
import json
import os
import re
import shutil
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

import database
from config import AppConfig

SNAPSHOT_NAME_RE = re.compile(r"^rev-\d{8}-[a-f0-9]{12}\.db$")
UPLOAD_KEY_RE = re.compile(r"^[a-f0-9-]{8,64}\.[a-z0-9]{1,10}$")
SHA256_HEX_RE = re.compile(r"^[a-f0-9]{64}$")


class StorageError(Exception):
    """Base error raised when durable persistence cannot guarantee safe state."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 503,
        error_code: str = "storage_error",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class StorageUnavailableError(StorageError):
    """Raised when R2 or restored vault state is unavailable or in ERROR/RESTORING state."""

    def __init__(self, message: str, *, error_code: str = "storage_unavailable") -> None:
        super().__init__(message, status_code=503, error_code=error_code)


class StorageCommitError(StorageError):
    """Raised when a mutation failed before or during durable R2 commit."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "storage_commit_failed",
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message, status_code=503, error_code=error_code)
        self.ambiguous = ambiguous


class StorageConflictError(StorageError):
    """Raised when conditional ETag commit detects another writer advanced manifest."""

    def __init__(self, message: str, *, error_code: str = "manifest_etag_conflict") -> None:
        super().__init__(message, status_code=503, error_code=error_code)


class StorageIdempotencyConflictError(StorageError):
    """Raised when an Idempotency-Key is reused with a different request payload."""

    def __init__(
        self,
        message: str = "Idempotency key already used with different payload",
        *,
        error_code: str = "idempotency_payload_mismatch",
    ) -> None:
        super().__init__(message, status_code=409, error_code=error_code)


@dataclass
class StorageUpload:
    """New Source File payload to upload durably to R2 before committing SQLite snapshot."""

    storage_key: str
    contents: bytes
    sha256: str
    content_type: str = "application/octet-stream"


class R2ClientProtocol(Protocol):
    """Protocol implemented by R2GatewayClient and in-memory test fakes."""

    def get_manifest(self) -> Tuple[int, Optional[Dict[str, Any]], str]: ...

    def put_manifest(
        self,
        manifest: Dict[str, Any],
        *,
        if_match: Optional[str] = None,
        if_none_match_star: bool = False,
    ) -> str: ...

    def get_snapshot(self, snapshot_key: str, dest_path: str) -> None: ...

    def put_snapshot(
        self,
        snapshot_key: str,
        snapshot_path: str,
        *,
        sha256: str,
        revision: int,
        schema_version: int,
    ) -> None: ...

    def delete_snapshot(self, snapshot_key: str) -> None: ...

    def get_upload(self, storage_key: str) -> Tuple[bytes, str]: ...

    def put_upload(
        self,
        storage_key: str,
        contents: bytes,
        *,
        sha256: str,
        content_type: str = "application/octet-stream",
    ) -> None: ...

    def delete_upload(self, storage_key: str) -> None: ...


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_bytes(dest_path: str, data: bytes) -> None:
    parent = os.path.dirname(os.path.abspath(dest_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{dest_path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, dest_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _atomic_copy_file(src_path: str, dest_path: str) -> None:
    parent = os.path.dirname(os.path.abspath(dest_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{dest_path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        shutil.copyfile(src_path, tmp_path)
        os.replace(tmp_path, dest_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def validate_upload_key(storage_key: str) -> str:
    cleaned = (storage_key or "").strip()
    if not UPLOAD_KEY_RE.match(cleaned):
        raise ValueError(f"Invalid storage key format: {storage_key!r}")
    return cleaned


def validate_snapshot_key(snapshot_key: str) -> str:
    cleaned = (snapshot_key or "").strip()
    if not SNAPSHOT_NAME_RE.match(cleaned):
        raise ValueError(f"Invalid snapshot key format: {snapshot_key!r}")
    return cleaned


class R2GatewayClient:
    """HTTP client communicating with Worker's internal VaultR2Gateway outbound handler."""

    def __init__(
        self,
        gateway_url: str,
        internal_token: str,
        container_epoch: str,
        timeout: float = 15.0,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.internal_token = internal_token
        self.container_epoch = container_epoch
        self.timeout = timeout

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        hdrs = {
            "X-Vault-Internal-Token": self.internal_token,
            "X-Vault-Container-Epoch": self.container_epoch,
        }
        if extra:
            hdrs.update(extra)
        return hdrs

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, bytes, Dict[str, str]]:
        url = f"{self.gateway_url}{path}"
        req = urllib.request.Request(
            url,
            data=data,
            headers=self._headers(headers),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, resp.read(), resp_headers
        except urllib.error.HTTPError as exc:
            err_body = b""
            try:
                err_body = exc.read()
            except Exception:
                pass
            resp_headers = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
            return exc.code, err_body, resp_headers
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StorageUnavailableError(
                f"R2 gateway unreachable during {method} {path}: {exc}",
                error_code="r2_gateway_unreachable",
            ) from exc

    @staticmethod
    def _extract_error_message(body: bytes, default: str) -> str:
        try:
            parsed = json.loads(body.decode("utf-8") or "{}")
            if isinstance(parsed, dict) and parsed.get("error"):
                return str(parsed["error"])
        except Exception:
            pass
        return default

    def get_manifest(self) -> Tuple[int, Optional[Dict[str, Any]], str]:
        status, body, headers = self._request("GET", "/manifest")
        if status == 200:
            try:
                parsed = json.loads(body.decode("utf-8"))
            except Exception as exc:
                raise StorageUnavailableError(
                    f"Corrupt manifest JSON in R2: {exc}",
                    error_code="corrupt_manifest_json",
                ) from exc
            if not isinstance(parsed, dict):
                raise StorageUnavailableError(
                    "Manifest payload is not a JSON object",
                    error_code="corrupt_manifest_json",
                )
            return 200, parsed, headers.get("x-r2-etag", "")
        if status == 404:
            return 404, None, ""
        if status == 409:
            raise StorageUnavailableError(
                "R2 bucket is non-empty but vault/manifest.json is missing",
                error_code="manifest_missing_on_non_empty_bucket",
            )
        err = self._extract_error_message(body, f"HTTP {status}")
        raise StorageUnavailableError(
            f"Failed to fetch R2 manifest ({status}): {err}",
            error_code=f"manifest_get_http_{status}",
        )

    def put_manifest(
        self,
        manifest: Dict[str, Any],
        *,
        if_match: Optional[str] = None,
        if_none_match_star: bool = False,
    ) -> str:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if if_none_match_star:
            headers["If-None-Match"] = "*"
        elif if_match:
            headers["If-Match"] = if_match
        else:
            raise StorageCommitError(
                "Manifest update requires If-Match or If-None-Match precondition",
                error_code="missing_manifest_precondition",
            )

        payload = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        try:
            status, body, _ = self._request("PUT", "/manifest", data=payload, headers=headers)
        except StorageUnavailableError as exc:
            raise StorageCommitError(
                str(exc),
                error_code=exc.error_code,
                ambiguous=True,
            ) from exc

        if status == 200:
            try:
                parsed = json.loads(body.decode("utf-8") or "{}")
            except Exception:
                parsed = {}
            return str(parsed.get("etag") or "").strip()
        if status == 412:
            raise StorageConflictError(
                "Manifest ETag precondition failed (412); another writer updated R2",
                error_code="manifest_etag_conflict",
            )
        err = self._extract_error_message(body, f"HTTP {status}")
        raise StorageCommitError(
            f"Manifest commit failed ({status}): {err}",
            error_code=f"manifest_put_http_{status}",
            ambiguous=(status >= 500),
        )

    def get_snapshot(self, snapshot_key: str, dest_path: str) -> None:
        clean_key = validate_snapshot_key(snapshot_key)
        status, body, headers = self._request("GET", f"/snapshots/{clean_key}")
        if status != 200:
            err = self._extract_error_message(body, f"HTTP {status}")
            raise StorageUnavailableError(
                f"Failed to download snapshot {clean_key} ({status}): {err}",
                error_code=f"snapshot_get_http_{status}",
            )
        header_sha = (headers.get("x-content-sha256") or "").strip().lower()
        if header_sha:
            actual_sha = hashlib.sha256(body).hexdigest()
            if actual_sha != header_sha:
                raise StorageUnavailableError(
                    f"Downloaded snapshot {clean_key} SHA-256 header mismatch",
                    error_code="snapshot_sha256_mismatch",
                )
        _atomic_write_bytes(dest_path, body)

    def put_snapshot(
        self,
        snapshot_key: str,
        snapshot_path: str,
        *,
        sha256: str,
        revision: int,
        schema_version: int,
    ) -> None:
        clean_key = validate_snapshot_key(snapshot_key)
        with open(snapshot_path, "rb") as fh:
            data = fh.read()
        status, body, _ = self._request(
            "PUT",
            f"/snapshots/{clean_key}",
            data=data,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(data)),
                "X-Content-Sha256": sha256,
                "X-Vault-Revision": str(revision),
                "X-Vault-Schema-Version": str(schema_version),
            },
        )
        if status != 200:
            err = self._extract_error_message(body, f"HTTP {status}")
            raise StorageCommitError(
                f"Failed to upload snapshot {clean_key} ({status}): {err}",
                error_code=f"snapshot_put_http_{status}",
            )

    def delete_snapshot(self, snapshot_key: str) -> None:
        clean_key = validate_snapshot_key(snapshot_key)
        status, body, _ = self._request("DELETE", f"/snapshots/{clean_key}")
        if status not in (200, 404):
            err = self._extract_error_message(body, f"HTTP {status}")
            raise StorageUnavailableError(
                f"Failed to delete snapshot {clean_key} ({status}): {err}",
                error_code=f"snapshot_delete_http_{status}",
            )

    def get_upload(self, storage_key: str) -> Tuple[bytes, str]:
        clean_key = validate_upload_key(storage_key)
        status, body, headers = self._request("GET", f"/uploads/{clean_key}")
        if status == 404:
            raise FileNotFoundError(f"Source file missing in R2: {clean_key}")
        if status != 200:
            err = self._extract_error_message(body, f"HTTP {status}")
            raise StorageUnavailableError(
                f"Failed to fetch Source File {clean_key} from R2 ({status}): {err}",
                error_code=f"upload_get_http_{status}",
            )
        return body, (headers.get("x-content-sha256") or "").strip().lower()

    def put_upload(
        self,
        storage_key: str,
        contents: bytes,
        *,
        sha256: str,
        content_type: str = "application/octet-stream",
    ) -> None:
        clean_key = validate_upload_key(storage_key)
        status, body, _ = self._request(
            "PUT",
            f"/uploads/{clean_key}",
            data=contents,
            headers={
                "Content-Type": content_type or "application/octet-stream",
                "Content-Length": str(len(contents)),
                "X-Content-Sha256": sha256,
            },
        )
        if status != 200:
            err = self._extract_error_message(body, f"HTTP {status}")
            raise StorageCommitError(
                f"Failed to upload Source File {clean_key} to R2 ({status}): {err}",
                error_code=f"upload_put_http_{status}",
            )

    def delete_upload(self, storage_key: str) -> None:
        clean_key = validate_upload_key(storage_key)
        status, body, _ = self._request("DELETE", f"/uploads/{clean_key}")
        if status not in (200, 404):
            err = self._extract_error_message(body, f"HTTP {status}")
            raise StorageUnavailableError(
                f"Failed to delete Source File {clean_key} from R2 ({status}): {err}",
                error_code=f"upload_delete_http_{status}",
            )


class VaultStorageCoordinator:
    """Central persistence coordinator enforcing restore gates, serialized commits, and R2 sync."""

    def __init__(
        self,
        config: AppConfig,
        client: Optional[R2ClientProtocol] = None,
    ) -> None:
        self.config = config
        self._client = client
        self._mutation_lock = threading.RLock()
        self._active_mutations = 0
        self.state: str = "BOOTING"
        self.error_code: str = ""
        self.error_detail: str = ""
        self.current_revision: int = 0
        self.manifest_etag: str = ""
        self._retained_snapshots: List[Dict[str, Any]] = []
        self._dirty_uncommitted: bool = False
        self._pending_commit: Optional[Dict[str, Any]] = None

    @property
    def is_busy(self) -> bool:
        return self._active_mutations > 0 or self.state == "RESTORING"

    @property
    def committed_db_path(self) -> str:
        return f"{self.config.db_path}.committed"

    def _get_client(self) -> R2ClientProtocol:
        if self._client is not None:
            return self._client
        if not self.config.r2_gateway_url:
            raise StorageUnavailableError(
                "R2_GATEWAY_URL is missing while R2 persistence is required",
                error_code="missing_r2_gateway_url",
            )
        if not self.config.internal_sync_token or not self.config.container_epoch:
            raise StorageUnavailableError(
                "INTERNAL_SYNC_TOKEN or CONTAINER_EPOCH is missing",
                error_code="missing_r2_gateway_credentials",
            )
        self._client = R2GatewayClient(
            gateway_url=self.config.r2_gateway_url,
            internal_token=self.config.internal_sync_token,
            container_epoch=self.config.container_epoch,
        )
        return self._client

    def _validate_manifest_dict(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        try:
            schema_version = int(manifest["schema_version"])
            revision = int(manifest["revision"])
            snapshot_size = int(manifest["snapshot_size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageUnavailableError(
                f"Manifest missing required numeric fields: {exc}",
                error_code="invalid_manifest",
            ) from exc

        if schema_version < 1 or schema_version > database.SUPPORTED_SCHEMA_VERSION:
            raise StorageUnavailableError(
                f"Unsupported manifest schema version {schema_version}",
                error_code="unsupported_schema_version",
            )
        if revision < 1:
            raise StorageUnavailableError(
                f"Invalid manifest revision {revision}",
                error_code="invalid_manifest",
            )
        if snapshot_size <= 0 or snapshot_size > database.MAX_SNAPSHOT_BYTES:
            raise StorageUnavailableError(
                f"Invalid manifest snapshot_size {snapshot_size}",
                error_code="invalid_manifest",
            )

        snapshot_key = str(manifest.get("snapshot_key") or "").strip()
        if not SNAPSHOT_NAME_RE.match(snapshot_key):
            raise StorageUnavailableError(
                f"Invalid manifest snapshot_key {snapshot_key!r}",
                error_code="invalid_manifest",
            )
        snapshot_sha256 = str(manifest.get("snapshot_sha256") or "").strip().lower()
        if not SHA256_HEX_RE.match(snapshot_sha256):
            raise StorageUnavailableError(
                "Invalid manifest snapshot_sha256",
                error_code="invalid_manifest",
            )

        retained = manifest.get("retained_snapshots")
        if not isinstance(retained, list) or not retained:
            retained = [
                {
                    "revision": revision,
                    "snapshot_key": snapshot_key,
                    "sha256": snapshot_sha256,
                    "size": snapshot_size,
                    "committed_at": str(manifest.get("committed_at") or _utc_iso()),
                }
            ]

        return {
            "schema_version": schema_version,
            "revision": revision,
            "snapshot_key": snapshot_key,
            "snapshot_sha256": snapshot_sha256,
            "snapshot_size": snapshot_size,
            "retained_snapshots": retained,
        }

    def _activate_validated_snapshot(self, tmp_path: str) -> None:
        database.remove_sqlite_sidecars(self.config.db_path)
        os.replace(tmp_path, self.config.db_path)
        database.init_db(self.config.db_path)
        snap_meta = database.export_db_snapshot(self.config.db_path, f"{self.committed_db_path}.tmp")
        _ = snap_meta
        os.replace(f"{self.committed_db_path}.tmp", self.committed_db_path)

    def startup(self) -> None:
        """Execute startup restore gate before watcher or HTTP routes accept vault traffic."""
        with self._mutation_lock:
            self.error_code = ""
            self.error_detail = ""
            self._dirty_uncommitted = False
            self._pending_commit = None

            if not self.config.is_r2_enabled():
                self.config.ensure_dirs()
                database.init_db(self.config.db_path)
                self.current_revision = 0
                self.manifest_etag = ""
                self._retained_snapshots = []
                self.state = "READY"
                return

            self.state = "RESTORING"
            restore_tmp = f"{self.config.db_path}.restore.{uuid.uuid4().hex[:8]}.tmp"
            try:
                self.config.ensure_dirs()
                client = self._get_client()
                status, manifest, etag = client.get_manifest()

                if status == 404:
                    if not self.config.r2_bootstrap_if_empty:
                        raise StorageUnavailableError(
                            "R2 bucket is uninitialized and R2_BOOTSTRAP_IF_EMPTY is false",
                            error_code="uninitialized_bucket",
                        )
                    self._bootstrap_empty_bucket(client)
                    self.state = "READY"
                    return

                if status != 200 or manifest is None:
                    raise StorageUnavailableError(
                        "Unexpected R2 manifest status during startup",
                        error_code="manifest_restore_failed",
                    )

                meta = self._validate_manifest_dict(manifest)
                client.get_snapshot(meta["snapshot_key"], restore_tmp)
                database.validate_sqlite_snapshot_file(
                    restore_tmp,
                    expected_size=meta["snapshot_size"],
                    expected_sha256=meta["snapshot_sha256"],
                )
                self._activate_validated_snapshot(restore_tmp)
                self.current_revision = meta["revision"]
                self.manifest_etag = etag
                self._retained_snapshots = list(meta["retained_snapshots"])
                self.state = "READY"
            except StorageError as exc:
                self.state = "ERROR"
                self.error_code = exc.error_code
                self.error_detail = str(exc)
            except Exception as exc:
                self.state = "ERROR"
                self.error_code = "restore_failed"
                self.error_detail = str(exc)
            finally:
                if os.path.exists(restore_tmp):
                    try:
                        os.remove(restore_tmp)
                    except OSError:
                        pass

    def _has_existing_local_uploads(self) -> bool:
        uploads_dir = self.config.uploads_dir
        if not uploads_dir or not os.path.isdir(uploads_dir):
            return False
        try:
            with os.scandir(uploads_dir) as it:
                for entry in it:
                    if not entry.name.startswith(".") and entry.is_file():
                        return True
        except OSError:
            return True
        return False

    def _bootstrap_empty_bucket(self, client: R2ClientProtocol) -> None:
        if database.has_any_vault_data(self.config.db_path) or self._has_existing_local_uploads():
            raise StorageUnavailableError(
                "Refusing to bootstrap empty R2 bucket because local Vault already contains existing data",
                error_code="bootstrap_refused_non_empty_local_db",
            )

        database.init_db(self.config.db_path)

        snap_tmp = f"{self.config.db_path}.bootstrap.{uuid.uuid4().hex[:8]}.tmp"
        snapshot_key = f"rev-{1:08d}-{uuid.uuid4().hex[:12]}.db"
        try:
            snap_meta = database.export_db_snapshot(self.config.db_path, snap_tmp)
            client.put_snapshot(
                snapshot_key,
                snap_tmp,
                sha256=snap_meta["sha256"],
                revision=1,
                schema_version=snap_meta["schema_version"],
            )
            now_iso = _utc_iso()
            retained = [
                {
                    "revision": 1,
                    "snapshot_key": snapshot_key,
                    "sha256": snap_meta["sha256"],
                    "size": snap_meta["size"],
                    "committed_at": now_iso,
                }
            ]
            manifest = {
                "schema_version": snap_meta["schema_version"],
                "revision": 1,
                "snapshot_key": snapshot_key,
                "snapshot_sha256": snap_meta["sha256"],
                "snapshot_size": snap_meta["size"],
                "committed_at": now_iso,
                "retained_snapshots": retained,
            }
            new_etag = client.put_manifest(manifest, if_none_match_star=True)
            _atomic_copy_file(snap_tmp, self.committed_db_path)
            self.current_revision = 1
            self.manifest_etag = new_etag
            self._retained_snapshots = retained
        finally:
            if os.path.exists(snap_tmp):
                try:
                    os.remove(snap_tmp)
                except OSError:
                    pass

    def _rollback_local_db_from_committed(self, extra_local_paths: Optional[List[str]] = None) -> None:
        if os.path.exists(self.committed_db_path):
            database.remove_sqlite_sidecars(self.config.db_path)
            _atomic_copy_file(self.committed_db_path, self.config.db_path)
        for path in extra_local_paths or []:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _reconcile_dirty_state(self) -> None:
        """Resolve ambiguous manifest commit status before allowing subsequent reads or writes."""
        pending = self._pending_commit
        if not self._dirty_uncommitted:
            return
        if not pending:
            self._rollback_local_db_from_committed()
            self._dirty_uncommitted = False
            return

        client = self._get_client()
        status, manifest, etag = client.get_manifest()
        if status != 200 or manifest is None:
            raise StorageUnavailableError(
                "Unable to verify manifest state after previous ambiguous commit",
                error_code="reconcile_manifest_unavailable",
            )

        meta = self._validate_manifest_dict(manifest)
        if (
            meta["revision"] == pending["revision"]
            and meta["snapshot_key"] == pending["snapshot_key"]
        ):
            snap_tmp = pending.get("snap_tmp_path")
            synced = False
            if snap_tmp and os.path.exists(snap_tmp):
                try:
                    _atomic_copy_file(snap_tmp, self.committed_db_path)
                    synced = True
                    try:
                        os.remove(snap_tmp)
                    except OSError:
                        pass
                except Exception:
                    synced = False

            if not synced:
                try:
                    database.export_db_snapshot(self.config.db_path, self.committed_db_path)
                    synced = True
                except Exception:
                    synced = False

            if not synced:
                restore_tmp = f"{self.config.db_path}.reconcile.{uuid.uuid4().hex[:8]}.tmp"
                try:
                    client.get_snapshot(meta["snapshot_key"], restore_tmp)
                    database.validate_sqlite_snapshot_file(
                        restore_tmp,
                        expected_size=meta["snapshot_size"],
                        expected_sha256=meta["snapshot_sha256"],
                    )
                    self._activate_validated_snapshot(restore_tmp)
                finally:
                    if os.path.exists(restore_tmp):
                        try:
                            os.remove(restore_tmp)
                        except OSError:
                            pass

            self.current_revision = meta["revision"]
            self.manifest_etag = etag
            self._retained_snapshots = list(meta["retained_snapshots"])
            self._dirty_uncommitted = False
            self._pending_commit = None
            try:
                self._run_post_commit_cleanup(
                    client=client,
                    cleanup_local_paths=pending.get("cleanup_local_paths") or [],
                    expired_snapshots=pending.get("expired_snapshots") or [],
                    retained_snapshots=self._retained_snapshots,
                )
            except Exception as cleanup_exc:
                print(f"[R2 Cleanup] Non-fatal reconciliation cleanup error: {cleanup_exc}")
            return

        if meta["revision"] == self.current_revision and etag == self.manifest_etag:
            self._rollback_local_db_from_committed(pending.get("new_local_paths"))
            snap_tmp = pending.get("snap_tmp_path")
            if snap_tmp and os.path.exists(snap_tmp):
                try:
                    os.remove(snap_tmp)
                except OSError:
                    pass
            self._dirty_uncommitted = False
            self._pending_commit = None
            return

        self.state = "ERROR"
        self.error_code = "manifest_etag_conflict"
        self.error_detail = (
            f"Remote manifest moved to revision {meta['revision']} ({meta['snapshot_key']}) "
            f"unexpectedly during reconciliation"
        )
        raise StorageConflictError(self.error_detail, error_code="manifest_etag_conflict")

    def require_ready(self) -> None:
        if self.state != "READY":
            raise StorageUnavailableError(
                f"Vault storage is not ready (state={self.state}, error={self.error_code or 'none'})",
                error_code=self.error_code or "storage_not_ready",
            )
        if self._dirty_uncommitted:
            with self._mutation_lock:
                if self.state != "READY":
                    raise StorageUnavailableError(
                        f"Vault storage is not ready (state={self.state})",
                        error_code=self.error_code or "storage_not_ready",
                    )
                if self._dirty_uncommitted:
                    self._reconcile_dirty_state()

    @staticmethod
    def _assert_idempotency_payload_match(existing_op: Dict[str, Any], expected_payload_hash: str) -> None:
        expected = (expected_payload_hash or "").strip().lower()
        stored = str(existing_op.get("payload_hash") or "").strip().lower()
        if expected and stored and expected != stored:
            raise StorageIdempotencyConflictError()

    def get_committed_operation(
        self,
        operation_id: str,
        *,
        expected_payload_hash: str = "",
    ) -> Optional[Dict[str, Any]]:
        clean_op_id = (operation_id or "").strip()
        if not clean_op_id:
            return None
        with self._mutation_lock:
            self.require_ready()
            row = database.get_vault_operation(clean_op_id, db_path=self.config.db_path)
            if not row:
                return None
            self._assert_idempotency_payload_match(row, expected_payload_hash)
            return row["result"]

    def _run_post_commit_cleanup(
        self,
        *,
        client: R2ClientProtocol,
        cleanup_local_paths: List[str],
        expired_snapshots: List[Dict[str, Any]],
        retained_snapshots: List[Dict[str, Any]],
    ) -> None:
        for local_path in cleanup_local_paths:
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except OSError:
                    pass

        for old_snap in expired_snapshots:
            old_key = str(old_snap.get("snapshot_key") or "").strip()
            if old_key and SNAPSHOT_NAME_RE.match(old_key):
                try:
                    client.delete_snapshot(old_key)
                except Exception as exc:
                    print(f"[R2 Cleanup] Deferred expired snapshot delete {old_key}: {exc}")

        retained_revs = [int(item["revision"]) for item in retained_snapshots if "revision" in item]
        if retained_revs:
            min_retained_rev = min(retained_revs)
            pending_deletions = database.list_pending_r2_deletions(
                max_revision=min_retained_rev,
                db_path=self.config.db_path,
            )
            for item in pending_deletions:
                storage_key = item["storage_key"]
                try:
                    client.delete_upload(storage_key)
                    database.remove_pending_r2_deletion(storage_key, db_path=self.config.db_path)
                except Exception as exc:
                    print(f"[R2 Cleanup] Deferred upload delete {storage_key}: {exc}")

    def execute_mutation(
        self,
        operation_id: str,
        op_type: str,
        mutate_fn: Callable[[int], Dict[str, Any]],
        *,
        new_upload: Optional[StorageUpload] = None,
        cleanup_local_paths_after_commit: Optional[List[str]] = None,
        payload_hash: str = "",
    ) -> Dict[str, Any]:
        """Serialize a state mutation and durably commit to R2 before returning."""
        clean_op_id = (operation_id or "").strip()
        clean_payload_hash = (payload_hash or "").strip().lower()
        cleanup_paths = cleanup_local_paths_after_commit if cleanup_local_paths_after_commit is not None else []

        with self._mutation_lock:
            self._active_mutations += 1
            try:
                self.require_ready()

                if clean_op_id:
                    existing_op = database.get_vault_operation(clean_op_id, db_path=self.config.db_path)
                    if existing_op:
                        self._assert_idempotency_payload_match(existing_op, clean_payload_hash)
                        return existing_op["result"]

                next_rev = self.current_revision + 1

                if not self.config.is_r2_enabled():
                    if new_upload is not None:
                        validate_upload_key(new_upload.storage_key)
                        dest = os.path.join(self.config.uploads_dir, new_upload.storage_key)
                        _atomic_write_bytes(dest, new_upload.contents)
                    res = mutate_fn(next_rev)
                    if clean_op_id:
                        database.record_vault_operation(
                            clean_op_id,
                            op_type,
                            str(res.get("doc_id") or res.get("id") or ""),
                            res,
                            next_rev,
                            db_path=self.config.db_path,
                            payload_hash=clean_payload_hash,
                        )
                    for path in cleanup_paths:
                        if path and os.path.exists(path):
                            try:
                                os.remove(path)
                            except OSError:
                                pass
                    self.current_revision = next_rev
                    return res

                client = self._get_client()
                if not os.path.exists(self.committed_db_path):
                    database.export_db_snapshot(self.config.db_path, self.committed_db_path)

                new_local_paths: List[str] = []
                snap_tmp_path = f"{self.config.db_path}.snap.{next_rev}.{uuid.uuid4().hex[:8]}.tmp"

                try:
                    if new_upload is not None:
                        clean_upload_key = validate_upload_key(new_upload.storage_key)
                        client.put_upload(
                            clean_upload_key,
                            new_upload.contents,
                            sha256=new_upload.sha256,
                            content_type=new_upload.content_type,
                        )
                        local_upload_path = os.path.join(self.config.uploads_dir, clean_upload_key)
                        _atomic_write_bytes(local_upload_path, new_upload.contents)
                        new_local_paths.append(local_upload_path)

                    self._dirty_uncommitted = True
                    res = mutate_fn(next_rev)
                    if clean_op_id:
                        database.record_vault_operation(
                            clean_op_id,
                            op_type,
                            str(res.get("doc_id") or res.get("id") or ""),
                            res,
                            next_rev,
                            db_path=self.config.db_path,
                            payload_hash=clean_payload_hash,
                        )

                    snap_meta = database.export_db_snapshot(self.config.db_path, snap_tmp_path)
                    snapshot_key = f"rev-{next_rev:08d}-{uuid.uuid4().hex[:12]}.db"
                    client.put_snapshot(
                        snapshot_key,
                        snap_tmp_path,
                        sha256=snap_meta["sha256"],
                        revision=next_rev,
                        schema_version=snap_meta["schema_version"],
                    )

                    now_iso = _utc_iso()
                    snap_entry = {
                        "revision": next_rev,
                        "snapshot_key": snapshot_key,
                        "sha256": snap_meta["sha256"],
                        "size": snap_meta["size"],
                        "committed_at": now_iso,
                    }
                    all_snaps = list(self._retained_snapshots) + [snap_entry]
                    retention = max(1, int(self.config.snapshot_retention_count))
                    retained = all_snaps[-retention:]
                    expired = all_snaps[:-retention] if len(all_snaps) > retention else []

                    manifest = {
                        "schema_version": snap_meta["schema_version"],
                        "revision": next_rev,
                        "snapshot_key": snapshot_key,
                        "snapshot_sha256": snap_meta["sha256"],
                        "snapshot_size": snap_meta["size"],
                        "committed_at": now_iso,
                        "retained_snapshots": retained,
                    }

                    self._pending_commit = {
                        "revision": next_rev,
                        "snapshot_key": snapshot_key,
                        "snap_tmp_path": snap_tmp_path,
                        "new_local_paths": new_local_paths,
                        "cleanup_local_paths": list(cleanup_paths),
                        "expired_snapshots": expired,
                    }

                    new_etag = client.put_manifest(manifest, if_match=self.manifest_etag)

                except StorageConflictError:
                    self.state = "ERROR"
                    self.error_code = "manifest_etag_conflict"
                    self.error_detail = "Manifest ETag conflict during commit"
                    if os.path.exists(snap_tmp_path):
                        try:
                            os.remove(snap_tmp_path)
                        except OSError:
                            pass
                    raise
                except StorageCommitError as exc:
                    if exc.ambiguous:
                        try:
                            self._reconcile_dirty_state()
                            if not self._dirty_uncommitted and self.current_revision == next_rev:
                                return res  # type: ignore[possibly-undefined]
                        except StorageError:
                            pass
                        raise
                    self._rollback_local_db_from_committed(new_local_paths)
                    self._dirty_uncommitted = False
                    self._pending_commit = None
                    if os.path.exists(snap_tmp_path):
                        try:
                            os.remove(snap_tmp_path)
                        except OSError:
                            pass
                    raise
                except StorageError:
                    self._rollback_local_db_from_committed(new_local_paths)
                    self._dirty_uncommitted = False
                    self._pending_commit = None
                    if os.path.exists(snap_tmp_path):
                        try:
                            os.remove(snap_tmp_path)
                        except OSError:
                            pass
                    raise
                except Exception as exc:
                    self._rollback_local_db_from_committed(new_local_paths)
                    self._dirty_uncommitted = False
                    self._pending_commit = None
                    if os.path.exists(snap_tmp_path):
                        try:
                            os.remove(snap_tmp_path)
                        except OSError:
                            pass
                    raise StorageCommitError(
                        f"Mutation failed before durable R2 commit: {exc}",
                        error_code="mutation_aborted",
                    ) from exc

                # Manifest commit on R2 succeeded above. Never rollback local DB after this point;
                # keep dirty/pending state intact on local finalization error so require_ready() reconciles.
                try:
                    _atomic_copy_file(snap_tmp_path, self.committed_db_path)
                    if os.path.exists(snap_tmp_path):
                        try:
                            os.remove(snap_tmp_path)
                        except OSError:
                            pass
                    self.current_revision = next_rev
                    self.manifest_etag = new_etag
                    self._retained_snapshots = retained
                    self._dirty_uncommitted = False
                    self._pending_commit = None
                except Exception as post_exc:
                    raise StorageCommitError(
                        f"Committed revision {next_rev} to R2 but local finalization failed: {post_exc}",
                        error_code="post_commit_sync_failed",
                        ambiguous=False,
                    ) from post_exc

                try:
                    self._run_post_commit_cleanup(
                        client=client,
                        cleanup_local_paths=list(cleanup_paths),
                        expired_snapshots=expired,
                        retained_snapshots=retained,
                    )
                except Exception as cleanup_exc:
                    print(f"[R2 Cleanup] Non-fatal post-commit cleanup error at rev {next_rev}: {cleanup_exc}")
                return res
            finally:
                self._active_mutations = max(0, self._active_mutations - 1)

    def ensure_local_source_file(self, doc: Dict[str, Any]) -> str:
        """Resolve and lazily restore an authorized document's Source File from R2 if absent locally."""
        self.require_ready()
        src_path = (doc.get("source_path") or "").strip()
        raw_storage_key = (doc.get("source_storage_key") or "").strip()
        if not raw_storage_key and src_path:
            raw_storage_key = os.path.basename(src_path)

        if not raw_storage_key:
            raise FileNotFoundError("Document has no Source File")

        storage_key = validate_upload_key(raw_storage_key)
        expected_sha256 = (doc.get("source_sha256") or "").strip().lower()

        uploads_dir = os.path.abspath(self.config.uploads_dir)
        key_local_path = os.path.abspath(os.path.join(uploads_dir, storage_key))
        if not key_local_path.startswith(uploads_dir + os.sep):
            raise ValueError("Invalid local upload path")

        candidates: List[str] = [key_local_path]
        if src_path:
            abs_src = os.path.abspath(src_path)
            if abs_src.startswith(uploads_dir + os.sep) and abs_src not in candidates:
                candidates.append(abs_src)

        for candidate in candidates:
            if os.path.isfile(candidate):
                if expected_sha256:
                    with open(candidate, "rb") as fh:
                        actual = hashlib.sha256(fh.read()).hexdigest()
                    if actual != expected_sha256:
                        continue
                return candidate

        if not self.config.is_r2_enabled():
            raise FileNotFoundError("Source file not found on local disk")

        client = self._get_client()
        file_bytes, r2_sha256 = client.get_upload(storage_key)
        actual_sha256 = hashlib.sha256(file_bytes).hexdigest()
        if expected_sha256 and actual_sha256 != expected_sha256:
            raise StorageUnavailableError(
                f"Restored Source File {storage_key} SHA-256 mismatch against database record",
                error_code="source_file_checksum_mismatch",
            )
        if r2_sha256 and actual_sha256 != r2_sha256:
            raise StorageUnavailableError(
                f"Restored Source File {storage_key} SHA-256 mismatch against R2 metadata",
                error_code="source_file_checksum_mismatch",
            )

        _atomic_write_bytes(key_local_path, file_bytes)
        return key_local_path
