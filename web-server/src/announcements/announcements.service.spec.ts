import { ListCacheService } from '../common/cache/list-cache.service';
import { AnnouncementsService } from './announcements.service';

describe('AnnouncementsService pending critical', () => {
  it('adds Phase-1 missing columns before querying is_published', async () => {
    const statements: string[] = [];
    const ds = {
      options: { type: 'postgres' },
      query: jest.fn(async (sql: string) => {
        statements.push(sql.replace(/\s+/g, ' ').trim());
        return [];
      }),
    };
    const service = new AnnouncementsService(
      { getTenantById: jest.fn().mockResolvedValue({ id: 't1', dbUri: 'postgres://x' }) } as any,
      { getOrCreate: jest.fn().mockResolvedValue(ds) } as any,
      { create: jest.fn() } as any,
      new ListCacheService(),
    );

    await expect(service.pendingCritical('t1', 'user-1')).resolves.toEqual([]);

    const joined = statements.join('\n');
    expect(joined).toContain('ADD COLUMN IF NOT EXISTS school_level');
    expect(joined).toContain('ADD COLUMN IF NOT EXISTS is_published');
    expect(joined).toContain('ADD COLUMN IF NOT EXISTS is_critical');
    expect(joined).toMatch(/WHERE a\.is_critical = true AND a\.is_published = true/);
  });

  it('runs schema DDL once per tenant on repeated pendingCritical calls', async () => {
    const statements: string[] = [];
    const ds = {
      options: { type: 'postgres' },
      query: jest.fn(async (sql: string) => {
        statements.push(sql.replace(/\s+/g, ' ').trim());
        return [];
      }),
    };
    const service = new AnnouncementsService(
      { getTenantById: jest.fn().mockResolvedValue({ id: 't1', dbUri: 'postgres://x' }) } as any,
      { getOrCreate: jest.fn().mockResolvedValue(ds) } as any,
      { create: jest.fn() } as any,
      new ListCacheService(),
    );

    await service.pendingCritical('t1', 'user-1');
    const ddlAfterFirst = statements.filter((sql) =>
      sql.includes('CREATE TABLE') || sql.includes('ADD COLUMN'),
    ).length;
    await service.pendingCritical('t1', 'user-1');
    const ddlAfterSecond = statements.filter((sql) =>
      sql.includes('CREATE TABLE') || sql.includes('ADD COLUMN'),
    ).length;

    expect(ddlAfterFirst).toBeGreaterThan(0);
    expect(ddlAfterSecond).toBe(ddlAfterFirst);
  });
});
