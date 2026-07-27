# Xueji Loop

学记教育家长端 / 学习闭环 H5 服务。

## Scope

This repository is a sanitized source snapshot. It excludes production database files, uploads, generated DOCX/audio files, logs, backups, API keys, cookies, and server certificates.

## Run locally

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt  # if present
python server.py
```

Create local environment variables from `.env.example` before production use.
