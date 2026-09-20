import { ListCacheService } from './list-cache.service';

describe('ListCacheService', () => {
  it('isolates tenant keys and serves cache hits', async () => {
    const cache = new ListCacheService();
    const loader = jest.fn(async () => ['value']);

    await expect(cache.getOrLoad('tenant:a:users:all', loader)).resolves.toEqual(['value']);
    await expect(cache.getOrLoad('tenant:a:users:all', loader)).resolves.toEqual(['value']);
    await expect(cache.getOrLoad('tenant:b:users:all', loader)).resolves.toEqual(['value']);
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it('coalesces concurrent misses and supports forced refresh', async () => {
    const cache = new ListCacheService();
    let resolve!: (value: string[]) => void;
    const loader = jest.fn(() => new Promise<string[]>((done) => { resolve = done; }));

    const first = cache.getOrLoad('tenant:a:classes:all', loader);
    const second = cache.getOrLoad('tenant:a:classes:all', loader);
    expect(loader).toHaveBeenCalledTimes(1);
    resolve(['fresh']);
    await expect(Promise.all([first, second])).resolves.toEqual([['fresh'], ['fresh']]);

    await expect(cache.getOrLoad('tenant:a:classes:all', async () => ['new'], true))
      .resolves.toEqual(['new']);
  });

  it('invalidates a cache family', async () => {
    const cache = new ListCacheService();
    await cache.getOrLoad('tenant:a:terms:all', async () => ['old']);
    cache.invalidate('tenant:a:terms:');
    await expect(cache.getOrLoad('tenant:a:terms:all', async () => ['new']))
      .resolves.toEqual(['new']);
  });

  it('does not repopulate stale data after invalidation and protects cached values', async () => {
    const cache = new ListCacheService();
    let resolve!: (value: string[]) => void;
    const pending = cache.getOrLoad('tenant:a:users:all', () => new Promise<string[]>((done) => {
      resolve = done;
    }));
    cache.invalidate('tenant:a:users:');
    resolve(['stale']);
    await pending;
    const fresh = await cache.getOrLoad('tenant:a:users:all', async () => ['fresh']);
    expect(fresh).toEqual(['fresh']);

    fresh.push('mutated');
    await expect(cache.getOrLoad('tenant:a:users:all', async () => ['unexpected']))
      .resolves.toEqual(['fresh']);
  });

  it('evicts entries when the bounded capacity is exceeded', async () => {
    const cache = new ListCacheService();
    for (let index = 0; index < 5001; index += 1) {
      await cache.getOrLoad(`tenant:a:entry:${index}`, async () => index);
    }
    const loader = jest.fn(async () => -1);
    await cache.getOrLoad('tenant:a:entry:0', loader);
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it('reloads after the entry TTL expires', async () => {
    const cache = new ListCacheService();
    const loader = jest.fn(async () => ['fresh']);
    await cache.getOrLoad('tenant:a:terms:ttl', async () => ['old'], false, 1_000);
    const frozen = Date.now() + 1_100;
    jest.spyOn(Date, 'now').mockReturnValue(frozen);
    await expect(cache.getOrLoad('tenant:a:terms:ttl', loader)).resolves.toEqual(['fresh']);
    expect(loader).toHaveBeenCalledTimes(1);
    (Date.now as jest.Mock).mockRestore();
  });
});
