import { DataSource } from 'typeorm';

/**
 * TypeORM's `.query()` does not automatically rewrite `?` placeholders for Postgres.
 * Most of this codebase uses `?` placeholders (MySQL style), so this helper normalizes
 * them to `$1..$n` when the active driver is Postgres.
 */
export function runDbQuery(
  ds: Pick<DataSource, 'options'> & { query: (sql: string, params?: any[]) => Promise<any> },
  sql: string,
  params?: any[],
): Promise<any> {
  const dbType = (ds as any)?.options?.type;
  if (!params || params.length === 0) {
    return ds.query(sql);
  }

  if (dbType !== 'postgres') {
    return ds.query(sql, params);
  }

  // Replace each `?` placeholder with `$1`, `$2`, ... in order.
  // Assumption: our SQL uses `?` only as parameter placeholders (not inside string literals).
  let idx = 0;
  const normalizedSql = sql.replace(/\?/g, () => {
    idx += 1;
    return `$${idx}`;
  });

  if (idx !== params.length) {
    // If mismatch, it's safer to throw than run a malformed query.
    throw new Error(`runDbQuery placeholder count mismatch: SQL has ${idx} placeholders but params has ${params.length}.`);
  }

  return ds.query(normalizedSql, params);
}

