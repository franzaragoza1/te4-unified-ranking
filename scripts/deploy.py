#!/usr/bin/env python3
"""Publica el sitio en Cloudflare Pages.

Copia solo los ficheros publicos a una carpeta dist/ y la sube con wrangler,
para que data/_private/ (perfil de navegador, 9 MB) y el codigo del scraper
nunca acaben en internet.

Uso:  python scripts/deploy.py [--project NOMBRE]
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")

# Lo unico que el navegador necesita.
FILES = ["index.html", "styles.css", "app.js"]
DIRS = [os.path.join("data", "history")]
DATA_FILES = [os.path.join("data", "current.json")]


def stage():
    if os.path.isdir(DIST):
        shutil.rmtree(DIST)
    os.makedirs(DIST)
    for f in FILES + DATA_FILES:
        src = os.path.join(ROOT, f)
        dst = os.path.join(DIST, f)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    for d in DIRS:
        src = os.path.join(ROOT, d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(DIST, d))
    total = sum(len(fs) for _, _, fs in os.walk(DIST))
    print(f"dist/ preparado: {total} ficheros")


def deploy(project):
    cmd = ["npx", "--yes", "wrangler", "pages", "deploy", DIST,
           "--project-name", project, "--commit-dirty=true"]
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT, shell=(os.name == "nt"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.environ.get("CF_PAGES_PROJECT", "the4-ranking"))
    ap.add_argument("--stage-only", action="store_true")
    args = ap.parse_args()
    stage()
    sys.exit(0 if args.stage_only else deploy(args.project))
