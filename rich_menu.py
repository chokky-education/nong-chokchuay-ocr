"""One definition for LINE provisioning and the browser simulator's eight tap areas."""
from urllib.parse import urlencode, urlsplit

IMAGE_PATH = "/static/rich_menu/menu.jpg"
WIDTH, HEIGHT = 1526, 1031
# Tile boundaries follow the approved artwork. The branding footer is not clickable.
TILES = (
    ("upload", "ส่งบิล / เอกสาร", (0, 0, 846, 682), "ส่งเอกสาร", ""),
    ("summary", "สรุปยอดเบิก", (846, 0, 336, 348), "ยอดรอเบิก", ""),
    ("expenses", "รายการบิล", (1182, 0, 344, 348), "", "expenses"),
    ("students", "แฟ้มเด็ก", (846, 348, 336, 334), "", "students"),
    ("review", "เอกสารรอตรวจ", (1182, 348, 344, 334), "รอตรวจ", ""),
    ("search", "ค้นหาเอกสาร", (0, 682, 526, 246), "ค้นหาเอกสาร", ""),
    ("settings", "ตั้งค่า", (526, 682, 486, 246), "", "settings"),
    ("help", "วิธีใช้", (1012, 682, 514, 246), "วิธีใช้", ""),
)


def build_rich_menu(base_url: str) -> dict:
    base_url = base_url.strip().rstrip("/")
    parts = urlsplit(base_url)
    local = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"}
    if (parts.scheme != "https" and not local) or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path:
        raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin (HTTP loopback allowed for local preview)")
    areas, items = [], []
    for key, label, rect, cmd, view in TILES:
        if view:
            action = {"type": "uri", "label": label,
                      "uri": f"{base_url}/?{urlencode({'view': view, 'openExternalBrowser': '1'})}"}
        else:
            action = {"type": "postback", "label": label, "displayText": label,
                      "data": urlencode({"action": "run_cmd", "cmd": cmd})}
            if key == "search":
                action.update(inputOption="openKeyboard", fillInText="ค้นหา ")
        bounds = dict(zip(("x", "y", "width", "height"), rect))
        areas.append({"bounds": bounds, "action": action})
        items.append({"key": key, "label": label, "bounds": bounds, "action": action})
    return {
        "menu": {"size": {"width": WIDTH, "height": HEIGHT}, "selected": True,
                 "name": "nong-chokchuay-8-v1", "chatBarText": "เมนูน้องโชค", "areas": areas},
        "image_path": IMAGE_PATH, "items": items,
    }
