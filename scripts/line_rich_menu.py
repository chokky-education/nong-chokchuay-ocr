"""Build offline; install/restore only when an operator explicitly invokes that command."""
import argparse
import json
import os
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rich_menu import IMAGE_PATH, build_rich_menu

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.line.me/v2/bot"


def line_request(method, path, token, *, image=None, payload=None):
    origin = "https://api-data.line.me/v2/bot" if image is not None else API
    headers = {"Authorization": f"Bearer {token}"}
    if image is not None:
        headers["Content-Type"] = "image/jpeg"
    try:
        response = requests.request(method, origin + path, headers=headers,
                                    json=payload, data=image, timeout=(5, 30))
    except requests.RequestException:
        # Never print exception request objects, headers, token, or response bodies.
        raise RuntimeError("LINE connection failed; inspect saved state and current default before retrying") from None
    if method == "GET" and response.status_code == 404:
        return None
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"LINE API rejected {method} {path} (HTTP {response.status_code})")
    return response.json() if response.content else {}


def install(base_url, token, state_file):
    if not base_url.startswith("https://"):
        raise ValueError("Installation requires an HTTPS origin")
    menu = build_rich_menu(base_url)["menu"]
    old = line_request("GET", "/user/all/richmenu", token)
    state = {"previous_default": (old or {}).get("richMenuId"), "new_menu_id": None}
    # Refuse to overwrite a previous rollback record. Save before any remote change.
    with state_file.open("x", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    line_request("POST", "/richmenu/validate", token, payload=menu)
    new = line_request("POST", "/richmenu", token, payload=menu)["richMenuId"]
    state["new_menu_id"] = new
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    image = (ROOT / IMAGE_PATH.lstrip("/")).read_bytes()
    line_request("POST", f"/richmenu/{new}/content", token, image=image)
    # Activation is last. Retain the old menu and all per-user links for rollback.
    line_request("POST", f"/user/all/richmenu/{new}", token)
    actual = line_request("GET", "/user/all/richmenu", token)
    if (actual or {}).get("richMenuId") != new:
        raise RuntimeError("Default menu verification failed; consult saved rollback record")
    return state


def restore(token, state_file):
    state = json.loads(state_file.read_text(encoding="utf-8"))
    previous = state["previous_default"]
    current = line_request("GET", "/user/all/richmenu", token)
    if (current or {}).get("richMenuId") != state["new_menu_id"]:
        raise RuntimeError("Current default differs from this installation; refusing to replace another release")
    if previous:
        line_request("POST", f"/user/all/richmenu/{previous}", token)
    else:
        line_request("DELETE", "/user/all/richmenu", token)
    return {"restored_default": previous}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "install", "restore"))
    parser.add_argument("--base-url", default="")
    parser.add_argument("--state-file", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "build":
            print(json.dumps(build_rich_menu(args.base_url)["menu"], ensure_ascii=False, indent=2))
            return
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
        if not token or not args.state_file:
            raise ValueError("Set LINE_CHANNEL_ACCESS_TOKEN in environment and provide --state-file")
        result = install(args.base_url, token, args.state_file) if args.command == "install" else restore(token, args.state_file)
        print(json.dumps(result))
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
