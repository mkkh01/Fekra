// سيرفر إنتاج بسيط بدون أي اعتماديات: يقدّم ملفات dist على Render.
// Render يمرر المنفذ عبر PORT ويتطلب الاستماع على 0.0.0.0.

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.join(__dirname, 'dist');
const PORT = Number(process.env.PORT) || 4173;
const HOST = '0.0.0.0';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.webmanifest': 'application/manifest+json',
};

function send(res, code, body, type) {
  res.writeHead(code, { 'Content-Type': type });
  res.end(body);
}

const server = http.createServer((req, res) => {
  try {
    const url = new URL(req.url || '/', 'http://x');
    const rel = decodeURIComponent(url.pathname).replace(/^\/+/, '');
    // منع الخروج من مجلد dist
    let file = path.normalize(path.join(DIST, rel));
    if (!file.startsWith(DIST)) return send(res, 403, 'Forbidden', 'text/plain');
    // مجلد ← index.html، وملف مفقود ← index.html (دعم SPA)
    if (fs.existsSync(file) && fs.statSync(file).isDirectory()) {
      file = path.join(file, 'index.html');
    }
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
      file = path.join(DIST, 'index.html');
    }
    const ext = path.extname(file).toLowerCase();
    const headers = { 'Content-Type': MIME[ext] || 'application/octet-stream' };
    if (file.includes(`${path.sep}assets${path.sep}`)) {
      headers['Cache-Control'] = 'public, max-age=31536000, immutable';
    } else {
      headers['Cache-Control'] = 'no-cache';
    }
    res.writeHead(200, headers);
    fs.createReadStream(file).pipe(res);
  } catch {
    send(res, 500, 'Server error', 'text/plain');
  }
});

server.listen(PORT, HOST, () => {
  console.log(`سير الممالك يعمل على http://${HOST}:${PORT}`);
});
