import { ConfigService } from '@nestjs/config';
import { ProviderSecretsService } from './provider-secrets.service';
import type { ControlApiClient } from './control-api.client';

function makeService(client: Partial<ControlApiClient>, env: Record<string, string> = {}) {
  const config = {
    get: (key: string) => env[key],
  } as unknown as ConfigService;
  return new ProviderSecretsService(client as ControlApiClient, config);
}

describe('ProviderSecretsService caching', () => {
  const payload = {
    capability: 'payment',
    providerId: 'paystack',
    mode: 'live',
    settings: {},
    secrets: { secret_key: 'sk_test' },
    secretVersions: { secret_key: 1 },
  };

  it('serves a warm cache hit without calling control again', async () => {
    const getProviderSecrets = jest.fn().mockResolvedValue(payload);
    const service = makeService({
      isConfigured: () => true,
      getProviderSecrets,
    });

    await expect(service.getSecrets('t1', 'payment')).resolves.toEqual(payload);
    await expect(service.getSecrets('t1', 'payment')).resolves.toEqual(payload);
    expect(getProviderSecrets).toHaveBeenCalledTimes(1);
  });

  it('coalesces concurrent misses onto a single control fetch', async () => {
    let resolveFetch!: (v: typeof payload) => void;
    const getProviderSecrets = jest.fn(
      () =>
        new Promise<typeof payload>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const service = makeService({
      isConfigured: () => true,
      getProviderSecrets,
    });

    const a = service.getSecrets('t1', 'payment');
    const b = service.getSecrets('t1', 'payment');
    expect(getProviderSecrets).toHaveBeenCalledTimes(1);
    resolveFetch(payload);
    await expect(Promise.all([a, b])).resolves.toEqual([payload, payload]);
  });

  it('invalidate(tenant) is O(1) and forces a refetch', async () => {
    const getProviderSecrets = jest
      .fn()
      .mockResolvedValueOnce(payload)
      .mockResolvedValueOnce({ ...payload, secrets: { secret_key: 'rotated' } });
    const service = makeService({
      isConfigured: () => true,
      getProviderSecrets,
    });

    await service.getSecrets('t1', 'payment');
    service.invalidate('t1');
    const next = await service.getSecrets('t1', 'payment');
    expect(next?.secrets.secret_key).toBe('rotated');
    expect(getProviderSecrets).toHaveBeenCalledTimes(2);
  });

  it('does not re-cache a fetch that lost a race with invalidate', async () => {
    let resolveFetch!: (v: typeof payload) => void;
    const getProviderSecrets = jest.fn(
      () =>
        new Promise<typeof payload>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const service = makeService({
      isConfigured: () => true,
      getProviderSecrets,
    });

    const pending = service.getSecrets('t1', 'payment');
    service.invalidate('t1');
    resolveFetch(payload);
    await pending;

    // After invalidate, the next read must hit control again (stale write discarded).
    getProviderSecrets.mockResolvedValueOnce({
      ...payload,
      secrets: { secret_key: 'fresh' },
    });
    const next = await service.getSecrets('t1', 'payment');
    expect(next?.secrets.secret_key).toBe('fresh');
    expect(getProviderSecrets).toHaveBeenCalledTimes(2);
  });

  it('returns null when control is not configured', async () => {
    const service = makeService({
      isConfigured: () => false,
      getProviderSecrets: jest.fn(),
    });
    await expect(service.getSecrets('t1', 'payment')).resolves.toBeNull();
  });
});
