import 'reflect-metadata';
import { DataSource, DataSourceOptions } from 'typeorm';
import { join } from 'path';
import { TenantConfigEntity } from '../control-plane/entities/tenant-config.entity';
import { UserTenantMappingEntity } from '../control-plane/entities/user-tenant-mapping.entity';
import { AuditLogEntity } from '../control-plane/entities/audit-log.entity';
import { RegistrationStagingEntity } from '../control-plane/entities/registration-staging.entity';

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

export function buildControlDataSourceOptions(dbUrl: string): DataSourceOptions {
  assertPostgresUrl(dbUrl, 'CONTROL_DB_URL');
  const useSsl = shouldUsePostgresSsl(dbUrl);
  return {
    type: 'postgres',
    url: normalizePostgresSslMode(dbUrl),
    synchronize: false,
    logging: false,
    entities: [TenantConfigEntity, UserTenantMappingEntity, AuditLogEntity, RegistrationStagingEntity],
    // Neon/Supabase often require SSL. `sslmode=require` is typically present on those URLs.
    ssl: useSsl ? { rejectUnauthorized: false } : undefined,
    migrations: [join(__dirname, '../control-migrations/*.{js,ts}')],
    migrationsRun: false,
  };
}

export async function runControlMigrations(dbUrl: string): Promise<void> {
  const options = buildControlDataSourceOptions(dbUrl);
  const ds = new DataSource(options);
  await ds.initialize();
  try {
    await ds.runMigrations();
  } finally {
    await ds.destroy();
  }
}

