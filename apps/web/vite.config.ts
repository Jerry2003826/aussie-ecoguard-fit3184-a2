import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          auth: ['aws-amplify/auth', 'aws-amplify'],
          react: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query'],
        },
      },
    },
  },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], clearMocks: true },
});
