import { Injectable, InternalServerErrorException } from '@nestjs/common';
import { TenantConfig } from './control-plane.types';
import { DataSource } from 'typeorm';
import {
  buildTenantDataSourceOptions,
  runTenantMigrations,
} from '../database/typeorm.config';
import { runDbQuery } from '../database/db-driver.util';
import { createHash } from 'crypto';
import { DEFAULT_GRADING_MATRIX } from '../academic/grading-matrix.defaults';

@Injectable()
export class TenantProvisioningService {
  private getTenantStorageStrategy(): 'database' | 'schema' {
    const strategy = (
      process.env.TENANT_STORAGE_STRATEGY || 'database'
    ).toLowerCase();
    return strategy === 'schema' ? 'schema' : 'database';
  }

  async provision(tenant: TenantConfig): Promise<void> {
    // Postgres superuser URL is needed for database creation in a DB-per-tenant model.
    // For backwards compatibility, we fall back to MYSQL_SUPER_URL if POSTGRES_SUPER_URL is not set.
    const superUrl =
      process.env.POSTGRES_SUPER_URL || process.env.MYSQL_SUPER_URL;
    if (!superUrl) {
      throw new InternalServerErrorException(
        'POSTGRES_SUPER_URL (or MYSQL_SUPER_URL) is not configured',
      );
    }

    const strategy = this.getTenantStorageStrategy();

    // 1) Prepare tenant storage (database-per-tenant or schema-per-tenant)
    const adminDs = new DataSource(buildTenantDataSourceOptions(superUrl));
    await adminDs.initialize();
    try {
      if (strategy === 'schema') {
        // In schema mode, tenant.dbName is used as the schema identifier.
        await adminDs.query(`CREATE SCHEMA IF NOT EXISTS "${tenant.dbName}";`);
      } else {
        const dbName = tenant.dbName;
        const res = await runDbQuery(
          adminDs,
          `SELECT 1 FROM pg_database WHERE datname = ? LIMIT 1`,
          [dbName],
        );
        if (!res || res.length === 0) {
          await adminDs.query(`CREATE DATABASE "${dbName}";`);
        }
      }
    } finally {
      await adminDs.destroy();
    }

    // 2) Run tenant migrations using TypeORM targeting tenant.dbUri
    await runTenantMigrations(tenant.dbUri);
  }

  private stableId(prefix: string, value: string) {
    return `${prefix}_${createHash('sha256')
      .update(value)
      .digest('hex')
      .slice(0, 24)}`;
  }

  async seedDefaultsForSchoolLevels(
    dbUri: string,
    selectedSchoolLevels: string[],
  ): Promise<void> {
    const selected = new Set(
      (selectedSchoolLevels || []).map((level) => String(level).toLowerCase()),
    );
    const includesNursery = selected.has('nursery');
    const includesPrimary = selected.has('primary');
    const includesSecondary =
      selected.has('secondary') || selected.has('jss') || selected.has('sss');

    if (!includesNursery && !includesPrimary && !includesSecondary) return;

    const ds = new DataSource(buildTenantDataSourceOptions(dbUri));
    await ds.initialize();
    try {
      const categories = [
        ['Science', 'science'],
        ['Art', 'art'],
        ['Commercial', 'commercial'],
      ] as const;
      for (const [name, slug] of categories) {
        await ds.query(
          `INSERT INTO academic_subject_categories (id, name, slug)
           VALUES ($1, $2, $3)
           ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, is_active = true`,
          [this.stableId('cat', slug), name, slug],
        );
      }
      await ds.query(
        `INSERT INTO academic_grading_matrix
           (id, ca_weight, exam_weight, grade_bands, updated_at)
         VALUES ('default', $1, $2, $3, CURRENT_TIMESTAMP)
         ON CONFLICT (id) DO NOTHING`,
        [
          DEFAULT_GRADING_MATRIX.caWeight,
          DEFAULT_GRADING_MATRIX.examWeight,
          JSON.stringify(DEFAULT_GRADING_MATRIX.gradeBands),
        ],
      );
      const levels = [
        ...(includesNursery ? Array.from({ length: 3 }, (_, i) => [
          `Nursery ${i + 1}`,
          `NUR${i + 1}`,
          'nursery',
        ]) : []),
        ...(includesPrimary ? Array.from({ length: 6 }, (_, i) => [
          `Primary ${i + 1}`,
          `PRI${i + 1}`,
          'primary',
        ]) : []),
        ...(includesSecondary ? Array.from({ length: 3 }, (_, i) => [
          `JSS ${i + 1}`,
          `JSS${i + 1}`,
          'jss',
        ]) : []),
        ...(includesSecondary ? Array.from({ length: 3 }, (_, i) => [
          `SSS ${i + 1}`,
          `SSS${i + 1}`,
          'sss',
        ]) : []),
      ] as const;
      for (const [name, code, level] of levels) {
        await ds.query(
          `INSERT INTO academic_classes (id, name, code, school_level)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (id) DO UPDATE SET is_active = true, archived_at = NULL`,
          [this.stableId('cls', code), name, code, level],
        );
      }
      const subjectGroups = [
        {
          level: 'nursery',
          classes: levels.filter((item) => item[2] === 'nursery'),
          subjects: [
            ['English Language', 'english_language'],
            ['Mathematics', 'mathematics'],
          ],
        },
        {
          level: 'primary',
          classes: levels.filter((item) => item[2] === 'primary'),
          subjects: [
            ['English Language', 'english_language'],
            ['Mathematics', 'mathematics'],
          ],
        },
        {
          level: 'secondary',
          classes: levels.filter((item) => item[2] === 'jss' || item[2] === 'sss'),
          subjects: [
            ['English Language', 'english_language'],
            ['Mathematics', 'mathematics'],
          ],
        },
      ].filter((group) =>
        (group.level === 'nursery' && includesNursery) ||
        (group.level === 'primary' && includesPrimary) ||
        (group.level === 'secondary' && includesSecondary),
      );
      for (const group of subjectGroups) {
        for (const [name, code] of group.subjects) {
        const category = /biology|chemistry|physics|science/i.test(name)
          ? 'science'
          : /economics|business/i.test(name)
          ? 'commercial'
          : 'art';
        await ds.query(
          `INSERT INTO academic_subjects (id, name, code, school_level, category_id, class_ids)
           SELECT $1, $2, $3, $4, id, $5
           FROM academic_subject_categories WHERE slug = $6
           ON CONFLICT (id) DO UPDATE SET
             school_level = EXCLUDED.school_level,
             class_ids = EXCLUDED.class_ids,
             is_active = true,
             archived_at = NULL`,
          [
            this.stableId('subj', group.level === 'primary' ? code : `${group.level}_${code}`),
            name,
            `${group.level}_${code}`,
            group.level,
            JSON.stringify(
              group.classes
                .map((item) => this.stableId('cls', item[1])),
            ),
            category,
          ],
        );
        }
      }
    } finally {
      await ds.destroy();
    }
  }
}
