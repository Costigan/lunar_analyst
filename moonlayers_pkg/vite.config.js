import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  build: {
    lib: {
      entry: resolve(__dirname, 'src/widget.js'),
      name: 'MoonLayers',
      fileName: 'index',
      formats: ['es']
    },
    outDir: 'moonlayers/static',
    emptyOutDir: false,
    rollupOptions: {
      output: {
        entryFileNames: 'index.js',
        assetFileNames: 'index.css',
        inlineDynamicImports: true
      }
    },
    sourcemap: true,
    minify: true
  }
});
