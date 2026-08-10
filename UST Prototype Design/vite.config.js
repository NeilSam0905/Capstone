import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Phase 3: dataService.js calls relative /api/... paths per BACKEND_TODO.md;
  // proxy them to the Flask backend (see ../backend) so no CORS is needed in dev.
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:5000',
    },
  },
})
