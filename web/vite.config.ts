import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 5173,
    watch: {
      usePolling: process.env.HABITAT_WATCH_POLLING === 'true',
    },
    proxy: {
      '/api': {
        target:
          process.env.HABITAT_API_TARGET ??
          loadEnv(mode, process.cwd(), 'HABITAT_').HABITAT_API_TARGET ??
          'http://127.0.0.1:8080',
        changeOrigin: true,
      },
      '/geoserver': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
}));
