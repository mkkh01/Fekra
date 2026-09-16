#!/usr/bin/env bash
# بناء اللعبة ونشرها على GitHub Pages (فرع gh-pages).
# الاستخدام: GITHUB_TOKEN=xxx ./deploy-pages.sh
# يمكن تخصيص: REPO=owner/name BRANCH=gh-pages
set -euo pipefail

REPO="${REPO:-mkkh01/Fekra}"
BRANCH="${BRANCH:-gh-pages}"
: "${GITHUB_TOKEN:?ضع التوكن في متغير البيئة GITHUB_TOKEN}"

cd "$(dirname "$0")"
echo "==> بناء اللعبة..."
npm run build >/dev/null 2>&1

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "==> تجهيز فرع النشر..."
git clone --depth 1 "https://github.com/$REPO.git" "$TMP" --quiet
cd "$TMP"
git checkout --orphan "$BRANCH" --quiet
git rm -rf . >/dev/null 2>&1 || true
cp -r "$OLDPWD/dist/." "$TMP/"
touch .nojekyll
git add -A
git -c user.name="Arena" -c user.email="arena@local" commit -m "deploy: $(date -u +%Y-%m-%dT%H:%M:%SZ)" --quiet
echo "==> الرفع..."
git push "https://x-access-token:$GITHUB_TOKEN@github.com/$REPO.git" "$BRANCH" --force --quiet 2>&1 | sed 's/x-access-token:[^@]*/x-access-token:[REDACTED]/g' || true

OWNER="${REPO%%/*}"
NAME="${REPO##*/}"
echo "تم النشر: https://$OWNER.github.io/$NAME/"
