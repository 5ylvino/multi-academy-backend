import 'reflect-metadata';
import { DataSource, DataSourceOptions } from 'typeorm';
import { join } from 'path';

function assertPostgresUrl(dbUrl: string, varName: string) {
  const lower = dbUrl.toLowerCase();
  if (!(lower.startsWith('postgres://') || lower.startsWith('postgresql://'))) {
    throw new Error(
      `${varName} must be a Postgres URL (starts with postgres:// or postgresql://). Got: ${dbUrl}`,
    );
  }
}

function shouldUsePostgresSsl(dbUrl: string): boolean {
  if (process.env.PG_SSL === 'true') return true;
  if (process.env.PG_SSL === 'false') return false;
  const lower = dbUrl.toLowerCase();
  // Neon and most managed Postgres URLs include an sslmode that requires TLS.
  return (
    lower.includes('sslmode=require') ||
    lower.includes('sslmode=verify-ca') ||
    lower.includes('sslmode=verify-full') ||
    lower.includes('sslmode=prefer') ||
    lower.includes('ssl=true')
  );
}

function normalizePostgresSslMode(dbUrl: string): string {
  try {
    const url = new URL(dbUrl);
    const mode = url.searchParams.get('sslmode');
    if (mode && ['prefer', 'require', 'verify-ca'].includes(mode)) {
      url.searchParams.set('sslmode', 'verify-full');
    }
    return url.toString();
  } catch {
    return dbUrl;
  }
}

function intFromEnv(name: string, fallback: number): number {
  const parsed = Number(process.env[name]);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : fallback;
}

/**
 * Per-tenant pool size. This is deliberately small: in a db-per-tenant
 * deployment the server holds one pool per active tenant, so the ceiling is
 * `pool size × active tenants` against a single Postgres `max_connections`
 * (commonly 100). The node-pg default of 10 exhausts that at ~10 tenants.
 */
export const TENANT_POOL_MAX = intFromEnv('TENANT_DB_POOL_MAX', 5);
const TENANT_POOL_IDLE_MS = intFromEnv('TENANT_DB_POOL_IDLE_MS', 30_000);
const TENANT_CONNECT_TIMEOUT_MS = intFromEnv('TENANT_DB_CONNECT_TIMEOUT_MS', 8_000);
const TENANT_STATEMENT_TIMEOUT_MS = intFromEnv('TENANT_DB_STATEMENT_TIMEOUT_MS', 15_000);

export function buildTenantDataSourceOptions(dbUrl: string): DataSourceOptions {
  assertPostgresUrl(dbUrl, 'TENANT DB URL');
  const useSsl = shouldUsePostgresSsl(dbUrl);
  return {
    type: 'postgres',
    url: normalizePostgresSslMode(dbUrl),
    synchronize: false,
    logging: false,
    entities: [],
    // Neon/Supabase often require SSL. `sslmode=require` is typically present on those URLs.
    ssl: useSsl ? { rejectUnauthorized: false } : undefined,
    // Load compiled migrations only. Loading `.ts` directly can bypass ts-node and
    // cause runtime syntax errors (e.g. `implements` being parsed as JS).
    migrations: [join(__dirname, '../migrations/*.{js,ts}')],
    migrationsRun: false,
    extra: {
      max: TENANT_POOL_MAX,
      // Release idle sockets so a quiet tenant stops holding server connections.
      idleTimeoutMillis: TENANT_POOL_IDLE_MS,
      // Fail fast instead of queueing forever when the database is saturated.
      connectionTimeoutMillis: TENANT_CONNECT_TIMEOUT_MS,
      // A runaway query must not pin a connection indefinitely.
      statement_timeout: TENANT_STATEMENT_TIMEOUT_MS,
      application_name: 'ma-sms-tenant',
      keepAlive: true,
    },
  };
}

export async function runTenantMigrations(dbUrl: string): Promise<void> {
  const options = buildTenantDataSourceOptions(dbUrl);
  const ds = new DataSource(options);
  await ds.initialize();
  try {
    await ds.runMigrations();
  } finally {
    await ds.destroy();
  }
}

