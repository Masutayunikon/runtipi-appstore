#!/usr/bin/env python3
"""
Runtipi Appstore Auto-Updater
- Détecte les nouvelles GitHub Releases
- Met à jour config.json, docker-compose.json, docker-compose.yml
- Incrémente tipi_version, met à jour updated_at
- Commit + push + notification Discord
"""

import os
import re
import json
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timezone

# ──────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────

APPSTORE_PATH      = Path(os.environ.get("APPSTORE_PATH", "."))
GITHUB_TOKEN       = os.environ.get("GH_TOKEN", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
AUTO_COMMIT        = True
GIT_COMMIT_MESSAGE = "chore: auto-update app versions [{date}]"
VERBOSE            = False

# ──────────────────────────────────────────────


def get_github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def get_latest_github_release(owner: str, repo: str) -> tuple[str | None, str | None]:
    """Retourne (tag_name, release_url) de la dernière release GitHub."""
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    try:
        resp = requests.get(url, headers=get_github_headers(), timeout=10)

        if resp.status_code == 404:
            # Pas de release formelle → fallback sur les tags
            url_tags = f"https://api.github.com/repos/{owner}/{repo}/tags"
            resp = requests.get(url_tags, headers=get_github_headers(), timeout=10)
            if resp.ok and resp.json():
                tag = resp.json()[0]
                return tag["name"], f"https://github.com/{owner}/{repo}/releases/tag/{tag['name']}"
            return None, None

        if not resp.ok:
            print(f"    ⚠️  GitHub API {resp.status_code} pour {owner}/{repo}")
            return None, None

        data = resp.json()
        return data.get("tag_name"), data.get("html_url")

    except requests.RequestException as e:
        print(f"    ⚠️  Requête échouée pour {owner}/{repo}: {e}")
        return None, None


def normalize_version(version: str) -> str:
    """
    Normalise un tag GitHub pour comparaison et stockage :
    - retire le préfixe 'v'       : 'v4.2.0'       → '4.2.0'
    - retire le préfixe 'release-': 'release-4.2.0' → '4.2.0'
    """
    version = version.strip()
    if version.lower().startswith("release-"):
        version = version[len("release-"):]
    return version.lstrip("v")


def now_timestamp_ms() -> int:
    """Timestamp Unix en millisecondes (format updated_at de Runtipi)."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# ── Mise à jour des fichiers ──────────────────

def update_config_json(path: Path, old_version: str, new_version: str) -> bool:
    """
    Modifie uniquement les champs version, tipi_version et updated_at
    via regex sur le texte brut — préserve le formatage original du fichier.
    """
    try:
        with open(path, "r") as f:
            text = f.read()

        # Lire les valeurs actuelles depuis le JSON parsé
        config   = json.loads(text)
        old_ver  = config.get("version", "")
        old_tipi = int(config.get("tipi_version", 0))

        new_ver  = make_new_tag(old_ver, normalize_version(new_version))
        new_tipi = old_tipi + 1
        new_ts   = now_timestamp_ms()

        # Remplacements regex ciblés — ne touche pas au reste du fichier
        text = re.sub(
            r'("version"\s*:\s*)"[^"]*"',
            rf'\1"{new_ver}"',
            text
        )
        text = re.sub(
            r'("tipi_version"\s*:\s*)\d+',
            rf'\g<1>{new_tipi}',
            text
        )
        text = re.sub(
            r'("updated_at"\s*:\s*)\d+',
            rf'\g<1>{new_ts}',
            text
        )

        with open(path, "w") as f:
            f.write(text)

        return True
    except Exception as e:
        print(f"    ❌ Erreur config.json ({path}): {e}")
        return False


def detect_image_tag_format(image: str, version_norm: str) -> str | None:
    """
    Détecte le tag exact utilisé dans l'image Docker (avec ou sans 'v').
    Ex: "app:v4.2.0" + "4.2.0" → "v4.2.0"
    Ex: "app:4.2.0"  + "4.2.0" → "4.2.0"
    Note: le préfixe 'release-' n'apparaît que sur GitHub, jamais dans les images Docker.
    """
    for candidate in [version_norm, f"v{version_norm}"]:
        if image.endswith(f":{candidate}"):
            return candidate
    return None


def make_new_tag(old_tag: str, new_version_norm: str) -> str:
    """
    Conserve le format du tag existant (avec ou sans 'v').
    Ex: old_tag="v4.2.0" → "v4.3.0"
    Ex: old_tag="4.2.0"  → "4.3.0"
    """
    return f"v{new_version_norm}" if old_tag.startswith("v") else new_version_norm


def update_docker_compose_json(path: Path, old_version: str, new_version: str) -> bool:
    """
    Remplace le tag de version uniquement sur le service isMain=true.
    Parse le JSON pour trouver l'image principale, puis str.replace ciblé
    sur le texte brut — préserve le formatage original du fichier.
    """
    if not path.exists():
        return False
    try:
        with open(path, "r") as f:
            text = f.read()

        data     = json.loads(text)
        old_norm = normalize_version(old_version)
        new_norm = normalize_version(new_version)

        # Trouver l'image du service principal (isMain: true)
        main_image = None
        for service in data.get("services", []):
            if service.get("isMain"):
                main_image = service.get("image", "")
                break

        if not main_image:
            return False

        # Détecter le format du tag dans cette image et construire le nouveau
        old_tag = detect_image_tag_format(main_image, old_norm)
        if not old_tag:
            return False

        new_tag   = make_new_tag(old_tag, new_norm)
        new_image = main_image[: -len(old_tag)] + new_tag

        # Remplacement ciblé sur l'image exacte — ne touche pas aux autres services
        new_text = text.replace(f'"image": "{main_image}"', f'"image": "{new_image}"', 1)

        if new_text != text:
            with open(path, "w") as f:
                f.write(new_text)
            return True

        return False
    except Exception as e:
        print(f"    ❌ Erreur docker-compose.json ({path}): {e}")
        return False


def update_docker_compose_yml(path: Path, old_version: str, new_version: str) -> bool:
    if not path.exists():
        return False
    try:
        with open(path, "r") as f:
            content = f.read()

        old_norm = normalize_version(old_version)
        new_norm = normalize_version(new_version)

        # Cherche le format exact utilisé dans le fichier (avec ou sans 'v')
        new_content = content
        for old_tag in [old_norm, f"v{old_norm}"]:
            if f":{old_tag}" in new_content:
                new_tag = make_new_tag(old_tag, new_norm)
                new_content = new_content.replace(f":{old_tag}", f":{new_tag}")

        if new_content != content:
            with open(path, "w") as f:
                f.write(new_content)
            return True

        return False
    except Exception as e:
        print(f"    ❌ Erreur docker-compose.yml ({path}): {e}")
        return False


# ── Git ───────────────────────────────────────

def git_stage_and_commit(updated_apps: list[str]):
    """Stage et commit les fichiers modifiés. Le push et la PR sont gérés par le workflow."""
    date_str  = datetime.now().strftime("%Y-%m-%d")
    short_msg = GIT_COMMIT_MESSAGE.replace("{date}", date_str)
    body      = "Apps mises à jour :\n" + "\n".join(f"  - {a}" for a in updated_apps)
    full_msg  = f"{short_msg}\n\n{body}"

    try:
        subprocess.run(["git", "-C", str(APPSTORE_PATH), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(APPSTORE_PATH), "commit", "-m", full_msg], check=True)
        print(f"\n✅ Git — commit OK ({len(updated_apps)} app(s))")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Erreur Git : {e}")


def set_github_actions_outputs(updated_apps: list[str], discord_updates: list[dict]):
    """Expose les outputs pour le step 'Create Pull Request' du workflow."""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        return  # pas dans GitHub Actions, on ignore

    lines = ["## 🚀 Apps mises à jour\n"]
    for u in discord_updates:
        lines.append(
            f"- **{u['app']}** : `{u['old_version']}` → `{u['new_version']}`"
            f" · tipi_version `{u['tipi_version']}`"
            f" · [Release]({u['release_url']})"
        )
    pr_body = "\n".join(lines)

    with open(github_output, "a") as f:
        f.write("updated=true\n")
        f.write("pr_body<<EOF\n")
        f.write(pr_body + "\n")
        f.write("EOF\n")


# ── Discord ───────────────────────────────────

def send_discord_notification(updates: list[dict]):
    if not DISCORD_WEBHOOK_URL or not updates:
        return

    lines = ["🚀 **Runtipi Appstore — Mises à jour**\n"]
    for u in updates:
        lines.append(
            f"• **{u['app']}** `{u['old_version']}` → `{u['new_version']}`"
            f"  ·  tipi_version → `{u['tipi_version']}`"
            f"  ·  [Release]({u['release_url']})"
        )

    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": "\n".join(lines)},
            timeout=10,
        )
        if resp.ok:
            print("📣 Notification Discord envoyée")
        else:
            print(f"⚠️  Discord webhook erreur {resp.status_code}")
    except requests.RequestException as e:
        print(f"⚠️  Discord notification échouée : {e}")


# ── Main ──────────────────────────────────────

def process_apps():
    app_dirs = sorted([d for d in (APPSTORE_PATH / "apps").iterdir() if d.is_dir()])

    if not app_dirs:
        print(f"❌ Aucune app trouvée dans {APPSTORE_PATH}/apps/")
        return

    print(f"🔍 {len(app_dirs)} app(s) trouvée(s)\n")

    updated_apps    = []
    discord_updates = []
    errors          = []

    for app_dir in app_dirs:
        config_path  = app_dir / "config.json"
        dc_json_path = app_dir / "docker-compose.json"
        dc_yml_path  = app_dir / "docker-compose.yml"

        if not config_path.exists():
            continue

        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except Exception:
            continue

        app_name        = config.get("name") or app_dir.name
        current_version = config.get("version", "")
        source          = config.get("source", "")

        if "github.com" not in source:
            if VERBOSE:
                print(f"  ⏭  {app_name} — source non-GitHub, ignoré")
            continue

        parts = source.rstrip("/").split("github.com/")[-1].split("/")
        if len(parts) < 2:
            print(f"  ⚠️  {app_name} — URL GitHub invalide : {source}")
            continue

        owner, repo = parts[0], parts[1]

        latest_tag, release_url = get_latest_github_release(owner, repo)
        if not latest_tag:
            errors.append(app_name)
            continue

        if normalize_version(current_version) == normalize_version(latest_tag):
            if VERBOSE:
                print(f"  ✔  {app_name} — à jour ({current_version})")
            continue

        print(f"  🆕 {app_name} : {current_version} → {latest_tag}")

        update_docker_compose_json(dc_json_path, current_version, latest_tag)
        update_docker_compose_yml(dc_yml_path,   current_version, latest_tag)
        ok = update_config_json(config_path, current_version, latest_tag)

        if ok:
            with open(config_path) as f:
                new_tipi = json.load(f).get("tipi_version", "?")

            print(f"     ✔ config.json       version={normalize_version(latest_tag)}, tipi_version={new_tipi}")
            print(f"     ✔ docker-compose.json")
            print(f"     ✔ docker-compose.yml")

            updated_apps.append(app_name)
            discord_updates.append({
                "app":          app_name,
                "old_version":  current_version,
                "new_version":  normalize_version(latest_tag),
                "tipi_version": new_tipi,
                "release_url":  release_url,
            })
        else:
            errors.append(app_name)

    print(f"\n{'─' * 50}")
    print(f"📦 Mises à jour : {len(updated_apps)}")
    if errors:
        print(f"⚠️  Erreurs       : {len(errors)} ({', '.join(errors)})")

    if updated_apps:
        if AUTO_COMMIT:
            git_stage_and_commit(updated_apps)
        set_github_actions_outputs(updated_apps, discord_updates)
        send_discord_notification(discord_updates)
    else:
        print("✅ Toutes les apps sont à jour.")


if __name__ == "__main__":
    print("=" * 50)
    print("  Runtipi Appstore Auto-Updater")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50 + "\n")
    process_apps()