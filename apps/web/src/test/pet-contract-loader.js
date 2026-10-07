// Vitest-only Node adapter. Production browser modules never import this file.
import { execFileSync } from 'node:child_process';
import { platform } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export function loadPetContract() {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..');
  const python = join(root, '.venv', platform() === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  return JSON.parse(
    execFileSync(python, [join(root, 'tests/backend/test_pet_result_contract.py'), '--emit'], {
      cwd: root,
      encoding: 'utf8',
      timeout: 10000,
    }),
  );
}
