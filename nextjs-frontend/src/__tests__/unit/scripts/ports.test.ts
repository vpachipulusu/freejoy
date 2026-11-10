import { describe, expect, it } from 'vitest';
import rawPorts from '../../../../scripts/ports.cjs';
import devKill from '../../../../scripts/dev-kill.cjs';
import e2eEnv from '../../../../scripts/e2e-env.cjs';

type Env = Record<string, string | undefined>;
const ports = rawPorts as unknown as {
  getDevPort(env: Env): number;
  getE2ePort(env: Env): number;
  getBackendPort(env: Env): number;
  getE2eBaseUrl(env: Env): string;
  getBackendApiUrl(env: Env): string;
};
const toNextProcessEnv = e2eEnv.toNextProcessEnv as (env: Env) => Env;

describe('ports', () => {
  it('keeps the defaults when nothing is set', () => {
    expect(ports.getDevPort({})).toBe(3000);
    expect(ports.getE2ePort({})).toBe(3000);
    expect(ports.getBackendPort({})).toBe(8000);
    expect(ports.getE2eBaseUrl({})).toBe('http://localhost:3000');
    expect(ports.getBackendApiUrl({})).toBe('http://127.0.0.1:8000');
  });

  it('treats an empty value as unset', () => {
    expect(ports.getE2ePort({ E2E_PORT: '' })).toBe(3000);
  });

  it('derives URLs from E2E_PORT and BACKEND_PORT', () => {
    const env = { E2E_PORT: '3100', BACKEND_PORT: '8100' };
    expect(ports.getE2eBaseUrl(env)).toBe('http://localhost:3100');
    expect(ports.getBackendApiUrl(env)).toBe('http://127.0.0.1:8100');
  });

  it('lets an explicit BACKEND_API_URL win over BACKEND_PORT', () => {
    const env = { BACKEND_PORT: '8100', BACKEND_API_URL: 'http://backend.test:9000' };
    expect(ports.getBackendApiUrl(env)).toBe('http://backend.test:9000');
  });

  it.each(['abc', '0', '70000', '30.5', '-1'])('rejects the invalid port %s', (value) => {
    expect(() => ports.getE2ePort({ E2E_PORT: value })).toThrow(/E2E_PORT/);
  });
});

describe('toNextProcessEnv client address', () => {
  it('trusts the e2e test header, so the e2e server gives each test its own rate-limit address', () => {
    expect(toNextProcessEnv({}).TRUSTED_CLIENT_IP_HEADER).toBe('x-e2e-client-ip');
  });

  it('does not let a developer env file choose a different trusted header for the e2e server', () => {
    expect(toNextProcessEnv({ TRUSTED_CLIENT_IP_HEADER: 'x-real-ip' }).TRUSTED_CLIENT_IP_HEADER).toBe(
      'x-e2e-client-ip'
    );
  });
});

describe('toNextProcessEnv ports', () => {
  it('sets PORT from E2E_PORT, not from the dev PORT', () => {
    const env = toNextProcessEnv({ PORT: '3555', E2E_PORT: '3100' });
    expect(env.PORT).toBe('3100');
    expect(env.BACKEND_API_URL).toBe('http://127.0.0.1:8000');
  });

  it('points BACKEND_API_URL at BACKEND_PORT unless set explicitly', () => {
    expect(toNextProcessEnv({ BACKEND_PORT: '8100' }).BACKEND_API_URL).toBe(
      'http://127.0.0.1:8100'
    );
    expect(
      toNextProcessEnv({ BACKEND_PORT: '8100', BACKEND_API_URL: 'http://x.test' }).BACKEND_API_URL
    ).toBe('http://x.test');
  });
});

describe('toNextProcessEnv catalogue source', () => {
  it('serves the fixture catalogue in e2e unless real data is asked for', () => {
    expect(toNextProcessEnv({}).CATALOGUE_SOURCE).toBe('fixture');
    expect(toNextProcessEnv({ E2E_LIVE_CATALOGUE: '0' }).CATALOGUE_SOURCE).toBe('fixture');
    expect(toNextProcessEnv({ CATALOGUE_SOURCE: 'api' }).CATALOGUE_SOURCE).toBe('fixture');
  });

  it('serves the API catalogue when E2E_LIVE_CATALOGUE=1', () => {
    expect(toNextProcessEnv({ E2E_LIVE_CATALOGUE: '1' }).CATALOGUE_SOURCE).toBe('api');
  });
});

describe('listeningPidsFromNetstat', () => {
  const output = [
    '  TCP    0.0.0.0:3000           0.0.0.0:0              LISTENING       111',
    '  TCP    0.0.0.0:30001          0.0.0.0:0              LISTENING       222',
    '  TCP    0.0.0.0:13000          0.0.0.0:0              LISTENING       333',
    '  TCP    [::]:3000              [::]:0                 LISTENING       111',
    '  TCP    127.0.0.1:3000         127.0.0.1:50000        ESTABLISHED     444',
    '  TCP    0.0.0.0:3100           0.0.0.0:0              LISTENING       555',
  ].join('\r\n');

  it('matches only the exact port that is listening', () => {
    expect([...devKill.listeningPidsFromNetstat(output, 3000)]).toEqual(['111']);
    expect([...devKill.listeningPidsFromNetstat(output, 3100)]).toEqual(['555']);
  });

  it('returns nothing when the port is free', () => {
    expect(devKill.listeningPidsFromNetstat(output, 4000).size).toBe(0);
  });
});
