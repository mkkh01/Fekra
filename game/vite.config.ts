import { defineConfig } from 'vite';

export default defineConfig({
  // مسارات نسبية: تعمل على GitHub Pages (‎/Fekra/‎) وعلى Render (الجذر) معًا
  base: './',
  server: {
    host: '0.0.0.0',
    port: 5173,
    allowedHosts: true,
  },
});
