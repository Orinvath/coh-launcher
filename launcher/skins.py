"""Strict skin manifests: validated colour tokens and image assets, no scripts.

A skin is a folder under skins/ with skin.json and an assets/ folder. Every
asset path must stay inside that folder, must not use links, and must be a
PNG, JPEG or WebP image of at most 20 MiB. Anything else rejects the skin.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import PROJECT_ROOT

SKINS_DIR = PROJECT_ROOT / "skins"
TOKEN_KEYS = ("base", "rail", "card", "border", "text", "muted", "accent", "accentDeep", "online")
ASSET_KEYS = ("background", "logo", "brand", "feature1", "feature2", "feature3", "feature4")
MANIFEST_KEYS = frozenset({"version", "id", "name", "tokens", "assets"})
MAX_MANIFEST_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class SkinError(Exception):
    """A skin failed validation. The message is shown in Settings."""


@dataclass(frozen=True)
class Skin:
    id: str
    name: str
    tokens: dict
    assets: dict  # asset key -> resolved absolute Path inside the skin folder


def image_mime(path):
    """Return the MIME type for a PNG, JPEG or WebP file, judged by its bytes."""
    with open(path, "rb") as handle:
        head = handle.read(16)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    raise SkinError("Images must be PNG, JPEG or WebP.")


def _asset_path(folder, key, value):
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 512:
        raise SkinError(f"Asset {key} must be a file path.")
    if ":" in value or "\\" in value:
        raise SkinError(f"Asset {key} must be a file inside the skin folder, not a URL.")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise SkinError(f"Asset {key} must stay inside the skin folder.")
    current = folder
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SkinError(f"Asset {key} uses a link, which is not allowed.")
    if not current.is_file():
        raise SkinError(f"Asset {key} was not found: {value}")
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(folder.resolve()):
        raise SkinError(f"Asset {key} points outside the skin folder.")
    if resolved.stat().st_size > MAX_IMAGE_BYTES:
        raise SkinError(f"Asset {key} is larger than 20 MiB.")
    image_mime(resolved)
    return resolved


def load_skin(folder):
    """Validate one skin folder and return a Skin, or raise SkinError."""
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise SkinError("The skin folder must be a real folder, not a link.")
    manifest = folder / "skin.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise SkinError("skin.json was not found.")
    if manifest.stat().st_size > MAX_MANIFEST_BYTES:
        raise SkinError("skin.json is too large.")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SkinError("skin.json is not valid JSON.") from exc
    if not isinstance(data, dict) or set(data) != MANIFEST_KEYS:
        raise SkinError("skin.json must contain only version, id, name, tokens and assets.")
    if type(data["version"]) is not int or data["version"] != 1:
        raise SkinError("Unknown skin version. Only version 1 is supported.")
    skin_id = data["id"]
    if not isinstance(skin_id, str) or not ID_RE.match(skin_id):
        raise SkinError("Skin id must use lowercase letters, digits and hyphens.")
    name = data["name"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise SkinError("Skin name must be 1 to 60 characters.")
    tokens = data["tokens"]
    if not isinstance(tokens, dict) or set(tokens) != set(TOKEN_KEYS):
        raise SkinError(f"tokens must contain exactly: {', '.join(TOKEN_KEYS)}.")
    for key in TOKEN_KEYS:
        if not isinstance(tokens[key], str) or not HEX_RE.match(tokens[key]):
            raise SkinError(f"Token {key} must be a six-digit hex colour such as #01060c.")
    assets = data["assets"]
    if not isinstance(assets, dict) or set(assets) != set(ASSET_KEYS):
        raise SkinError(f"assets must contain exactly: {', '.join(ASSET_KEYS)}.")
    resolved = {key: _asset_path(folder, key, assets[key]) for key in ASSET_KEYS}
    return Skin(
        id=skin_id,
        name=name.strip(),
        tokens={key: tokens[key].lower() for key in TOKEN_KEYS},
        assets=resolved,
    )


def scan_skins(root=SKINS_DIR):
    """Return (valid skins, errors). Each error is a dict with folder and message."""
    root = Path(root)
    skins, errors, seen = [], [], set()
    if not root.is_dir():
        return skins, errors
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            skin = load_skin(child)
        except SkinError as exc:
            errors.append({"folder": child.name, "message": str(exc)})
            continue
        if skin.id in seen:
            errors.append({"folder": child.name, "message": f"Skin id {skin.id} is already used."})
            continue
        seen.add(skin.id)
        skins.append(skin)
    return skins, errors
