#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');

// Use the project-local tsx binary so it works even when tsx isn't in PATH
const tsx = process.platform === 'win32'
  ? join(root, 'node_modules', '.bin', 'tsx.cmd')
  : join(root, 'node_modules', '.bin', 'tsx');

const cliPath = join(root, 'src', 'cli.ts');

const result = spawnSync(tsx, [cliPath, ...process.argv.slice(2)], {
  stdio: 'inherit',
  shell: process.platform === 'win32',
});
process.exit(result.status ?? 0);
