#!/bin/sh
# Phase 18: one-shot migration job for the production compose stack.
# Runs as the admin role (DATABASE_URL), never as app_runtime.
set -eu
alembic upgrade head
python scripts/set_app_runtime_password.py
