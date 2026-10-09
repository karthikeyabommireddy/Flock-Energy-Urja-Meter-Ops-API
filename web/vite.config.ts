import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// strictPort: the API only allows the origins http://localhost:5173 and http://127.0.0.1:5173
// (URJA_CORS_ALLOW_ORIGINS), so a dev server that quietly moved to another port would just fail every request with a
// CORS error.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
