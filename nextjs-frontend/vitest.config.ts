import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    globals: true,
    exclude: ['**/node_modules/**', '**/dist/**', '**/e2e/**', '**/*.spec.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: [
        'src/lib/**/*.{ts,tsx}',
        'src/components/**/*.{ts,tsx}',
        'src/app/actions/**/*.{ts,tsx}',
      ],
      exclude: [
        'src/lib/api/generated/**',
        'src/**/*.d.ts',
        'src/**/*.test.{ts,tsx}',
        'src/**/*.spec.{ts,tsx}',
        'src/**/__tests__/e2e/**',
      ],
      // Coverage ratchet: the thresholds sit about 2 points under the measured
      // coverage (FREEJ-28 review fixes: 91.51 statements, 90.27 branches, 89.73 functions, 92.32 lines),
      // so CI fails when coverage drops. Raise them when measured coverage rises and
      // never lower them; the project minimum is 80%. To raise them to the current
      // values in one step, run:
      //   COVERAGE_RATCHET=1 npm run test:coverage
      // (Vitest rewrites the numbers below when coverage is higher; then leave a
      // margin of about 2 points under the measured values.)
      thresholds: {
        autoUpdate: process.env.COVERAGE_RATCHET === '1',
        branches: 88,
        functions: 88,
        lines: 91,
        statements: 90,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
