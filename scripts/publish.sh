#!/usr/bin/env bash
# altscodex-sdk PyPI 게시 스크립트 — 클린 → 빌드 → 검증 → 업로드
#
# Usage:
#   scripts/publish.sh           # production: https://pypi.org
#   scripts/publish.sh --test    # staging:    https://test.pypi.org
#   scripts/publish.sh --check   # build + twine check only, no upload
#
# Requirements:
#   - python3 with pip
#   - PyPI account at https://pypi.org (or https://test.pypi.org)
#   - API token configured. Easiest: ~/.pypirc with [pypi] / [testpypi] sections,
#     or set TWINE_USERNAME=__token__ and TWINE_PASSWORD=<token>.

set -euo pipefail

cd "$(dirname "$0")/.."

# 프로젝트 venv 가 있으면 그쪽 python 을 우선 사용 (tomllib 가 3.11+ 에서만 동작)
if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

MODE="prod"
case "${1:-}" in
  --test)  MODE="test" ;;
  --check) MODE="check" ;;
  "")      MODE="prod" ;;
  *) echo "Unknown flag: $1" >&2; exit 2 ;;
esac

NAME=$("$PY" -c "import tomllib; print(tomllib.loads(open('pyproject.toml','rb').read().decode())['project']['name'])")
VERSION=$("$PY" -c "import tomllib; print(tomllib.loads(open('pyproject.toml','rb').read().decode())['project']['version'])")

echo "==> Package: $NAME @ $VERSION"
echo "==> Mode:    $MODE"

# 1) 빌드 도구 설치 (없으면)
"$PY" -m pip install --quiet --upgrade build twine

# 2) 이전 산출물 정리
rm -rf dist/ build/
find . -name "*.egg-info" -type d -prune -exec rm -rf {} +

# 3) 소스/휠 빌드
"$PY" -m build

# 4) 메타데이터 + README 렌더링 검증 (PyPI 가 거부할 만한 문제를 미리 잡음)
"$PY" -m twine check dist/*

echo
echo "==> Built artifacts:"
ls -1 dist/

if [[ "$MODE" == "check" ]]; then
  echo "==> --check mode: skipping upload."
  exit 0
fi

# 5) 업로드 직전 확인
TARGET_URL="https://upload.pypi.org/legacy/"
TARGET_LABEL="PyPI (production)"
TWINE_REPO_ARG=""
if [[ "$MODE" == "test" ]]; then
  TARGET_URL="https://test.pypi.org/legacy/"
  TARGET_LABEL="TestPyPI (staging)"
  TWINE_REPO_ARG="--repository testpypi"
fi

echo
echo "==> About to upload $NAME-$VERSION to: $TARGET_LABEL"
echo "==> URL: $TARGET_URL"
read -r -p "Continue? [y/N] " ans
[[ "$ans" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }

# shellcheck disable=SC2086
"$PY" -m twine upload $TWINE_REPO_ARG dist/*

echo
echo "==> Done."
if [[ "$MODE" == "test" ]]; then
  echo "    Verify install: pip install --index-url https://test.pypi.org/simple/ $NAME==$VERSION"
else
  echo "    Verify install: pip install $NAME==$VERSION"
fi
