import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: path.resolve(currentDirectory, '../extension/media/webview'),
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: { output: { manualChunks: { flow: ['@xyflow/react'] } } }
  },
  test: { environment: 'jsdom', setupFiles: './src/testSetup.ts' }
});
