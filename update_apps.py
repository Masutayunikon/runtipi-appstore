#!/usr/bin/env python3
"""
Runtipi Appstore Auto-Updater
- Détecte les nouvelles GitHub Releases
- Met à jour config.json, docker-compose.json, docker-compose.yml
- Incrémente tipi_version, met à jour updated_at
- Commit + push + notification Discord
"""

import os
import json
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timezone

# ──────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────

# Chemin racine de ton fork du community-app-store
APPSTORE_PATH = Path(os.environ.get("APPSTORE_PATH", "/path/to/your/appstore"))

# Token GitHub (recommandé : évite le rate-limit de 60 req/h non authentifié)
# Créer sur https://github.com/settings/tokens (aucun scope nécessaire pour les repos publics)
GITHUB_TOKEN = os.environ.get("GH_TOKEN", "")

# Webhook Discord (laisser vide pour désactiver)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Commit & push automatique
AUTO_COMMIT = True
GIT_COMMIT_MESSAGE = "chore: auto-update app versions [{date}]"

# Afficher les apps déjà à jour
VERBOSE = False

# ──────────────────────────────────────────────


def get_github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def get_latest_github_release(owner: str, repo: str) -> tuple[str | None, str | None]:
    """
    Retourne (tag_name, release_url) de la dernière release GitHub.
    Fallback sur les tags si aucune release formelle n'existe.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    try:
        resp = requests.get(url, headers=get_github_headers(), timeout=10)

        if resp.status_code == 404:
            # Pas de release → fallback sur les tags
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
    """Retire le préfixe 'v' pour comparer : 'v4.2.0' == '4.2.0'."""
    return version.lstrip("v").strip()


def now_timestamp_ms() -> int:
    """Timestamp Unix en millisecondes (comme dans updated_at de Runtipi)."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# ── Mise à jour des fichiers ──────────────────

def update_config_json(path: Path, old_version: str, new_version: str) -> bool:
    """
    Met à jour config.json :
      - version      : nouvelle version (sans préfixe 'v')
      - tipi_version : +1
      - updated_at   : timestamp ms actuel
    """
    try:
        with open(path, "r") as f:
            config = json.load(f)

        config["version"]      = normalize_version(new_version)
        config["tipi_version"] = int(config.get("tipi_version", 0)) + 1
        config["updated_at"]   = now_timestamp_ms()

        with open(path, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")

        return True
    except Exception as e:
        print(f"    ❌ Erreur config.json ({path}): {e}")
        return False


def update_docker_compose_json(path: Path, old_version: str, new_version: str) -> bool:
    """
    Remplace le tag de version dans le champ "image" du docker-compose.json.
    Ex: "ghcr.io/wizarrrr/wizarr:4.2.0" → "ghcr.io/wizarrrr/wizarr:4.3.0"
    Gère les versions avec ou sans préfixe 'v'.
    """
    if not path.exists():
        return False
    try:
        with open(path, "r") as f:
            data = json.load(f)

        old_norm = normalize_version(old_version)
        new_norm = normalize_version(new_version)

        modified = False
        for service in data.get("services", []):
            image = service.get("image", "")
            if not image:
                continue

            for old_tag in [old_norm, f"v{old_norm}"]:
                if image.endswith(f":{old_tag}"):
                    new_tag = f"v{new_norm}" if old_tag.startswith("v") else new_norm
                    service["image"] = image[: -len(old_tag)] + new_tag
                    modified = True
                    break

        if modified:
            with open(path, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")

        return modified
    except Exception as e:
        print(f"    ❌ Erreur docker-compose.json ({path}): {e}")
        return False


def update_docker_compose_yml(path: Path, old_version: str, new_version: str) -> bool:
    """
    Remplace le tag de version dans les lignes 'image:' du docker-compose.yml.
    Remplacement de chaîne simple : :4.2.0 → :4.3.0
    """
    if not path.exists():
        return False
    try:
        with open(path, "r") as f:
            content = f.read()

        old_norm = normalize_version(old_version)
        new_norm = normalize_version(new_version)

        new_content = content
        for old_tag in [old_norm, f"v{old_norm}"]:
            new_tag = f"v{new_norm}" if old_tag.startswith("v") else new_norm
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

def git_commit_and_push(updated_apps: list[str]):
    date_str = datetime.now().strftime("%Y-%m-%d")
    short_msg = GIT_COMMIT_MESSAGE.replace("{date}", date_str)
    body = "Apps mises à jour :\n" + "\n".join(f"  - {a}" for a in updated_apps)
    full_msg = f"{short_msg}\n\n{body}"

    try:
        subprocess.run(["git", "-C", str(APPSTORE_PATH), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(APPSTORE_PATH), "commit", "-m", full_msg], check=True)
        subprocess.run(["git", "-C", str(APPSTORE_PATH), "push"], check=True)
        print(f"\n✅ Git — commit & push OK ({len(updated_apps)} app(s))")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Erreur Git : {e}")


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

        # ── Extraction owner/repo depuis l'URL GitHub ──
        if "github.com" not in source:
            if VERBOSE:
                print(f"  ⏭  {app_name} — source non-GitHub, ignoré")
            continue

        parts = source.rstrip("/").split("github.com/")[-1].split("/")
        if len(parts) < 2:
            print(f"  ⚠️  {app_name} — URL GitHub invalide : {source}")
            continue

        owner, repo = parts[0], parts[1]

        # ── Vérification GitHub ──
        latest_tag, release_url = get_latest_github_release(owner, repo)
        if not latest_tag:
            errors.append(app_name)
            continue

        if normalize_version(current_version) == normalize_version(latest_tag):
            if VERBOSE:
                print(f"  ✔  {app_name} — à jour ({current_version})")
            continue

        # ── Mise à jour des 3 fichiers ──
        print(f"  🆕 {app_name} : {current_version} → {latest_tag}")

        # docker-compose en premier (ils lisent l'ancienne version), config.json en dernier
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

    # ── Résumé ──
    print(f"\n{'─' * 50}")
    print(f"📦 Mises à jour : {len(updated_apps)}")
    if errors:
        print(f"⚠️  Erreurs       : {len(errors)} ({', '.join(errors)})")

    if updated_apps:
        if AUTO_COMMIT:
            git_commit_and_push(updated_apps)
        send_discord_notification(discord_updates)
    else:
        print("✅ Toutes les apps sont à jour.")


if __name__ == "__main__":
    print("=" * 50)
    print("  Runtipi Appstore Auto-Updater")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50 + "\n")
    process_apps()