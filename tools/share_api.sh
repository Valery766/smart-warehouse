#!/usr/bin/env bash
set -e

if ! command -v brew >/dev/null 2>&1; then
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile || true
  eval "$(/opt/homebrew/bin/brew shellenv)" || true
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  brew install cloudflare/cloudflare/cloudflared
fi

echo "Проверяю локальный бэкенд на http://localhost:3001/health ..."
curl -fsS http://localhost:3001/health >/dev/null
echo "Ок. Стартую публичный туннель. Скопируйте ссылку https://... из вывода ниже."
cloudflared tunnel --url http://localhost:3001
