import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { ListCacheService } from '../common/cache/list-cache.service';
import {
  DEFAULT_GRADING_MATRIX,
  DEFAULT_GRADE_BANDS,
  type GradeBand,
} from './grading-matrix.defaults';

const DEFAULT_MATRIX = DEFAULT_GRADING_MATRIX;

@Injectable()
export class GradingMatrixService {
  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
    private readonly listCache: ListCacheService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS academic_grading_matrix (
        id varchar(64) PRIMARY KEY DEFAULT 'default',
        ca_weight int NOT NULL DEFAULT 40,
        exam_weight int NOT NULL DEFAULT 60,
        grade_bands text NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async get(tenantId: string, force = false) {
    await this.flags.assertEnabled(tenantId, 'academic.grading_matrix');
    return this.listCache.getOrLoad(`tenant:${tenantId}:academic:grading-matrix`, async () => {
      const ds = await this.getTenantDs(tenantId);
      await this.ensure(ds);
      const rows: any[] = await runDbQuery(
        ds,
        `SELECT ca_weight as "caWeight", exam_weight as "examWeight", grade_bands as "gradeBands"
         FROM academic_grading_matrix WHERE id = 'default' LIMIT 1`,
        [],
      );
      if (!rows?.[0]) return DEFAULT_MATRIX;
      return {
        caWeight: Number(rows[0].caWeight),
        examWeight: Number(rows[0].examWeight),
        gradeBands: parseBands(rows[0].gradeBands),
      };
    }, force);
  }

  async set(
    tenantId: string,
    body: { caWeight: number; examWeight: number; gradeBands?: GradeBand[] },
  ) {
    await this.flags.assertEnabled(tenantId, 'academic.grading_matrix');
    const caWeight = Number(body.caWeight);
    const examWeight = Number(body.examWeight);
    if (
      !Number.isFinite(caWeight) ||
      !Number.isFinite(examWeight) ||
      caWeight < 0 ||
      examWeight < 0 ||
      caWeight > 100 ||
      examWeight > 100 ||
      caWeight + examWeight !== 100
    ) {
      throw new BadRequestException('caWeight + examWeight must equal 100');
    }
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const existing: any[] = await runDbQuery(
      ds,
      `SELECT id, grade_bands FROM academic_grading_matrix WHERE id = 'default' LIMIT 1`,
      [],
    );
    const bands =
      body.gradeBands ??
      (existing?.[0]
        ? parseBands(existing[0].grade_bands)
        : DEFAULT_MATRIX.gradeBands);
    validateGradingBands(bands);
    if (existing?.[0]) {
      await runDbQuery(
        ds,
        `UPDATE academic_grading_matrix SET ca_weight = ?, exam_weight = ?, grade_bands = ?, updated_at = NOW() WHERE id = 'default'`,
        [caWeight, examWeight, JSON.stringify(bands)],
      );
    } else {
      await runDbQuery(
        ds,
        `INSERT INTO academic_grading_matrix (id, ca_weight, exam_weight, grade_bands, updated_at)
         VALUES ('default', ?, ?, ?, NOW())`,
        [caWeight, examWeight, JSON.stringify(bands)],
      );
    }
    this.listCache.invalidate(`tenant:${tenantId}:academic:grading-matrix`);
    return this.get(tenantId, true);
  }

  /** Apply weights when matrix feature is on; otherwise return inputs unchanged. */
  async computeTotal(
    tenantId: string,
    caScore: number | null,
    examScore: number | null,
  ): Promise<{ total: number | null; grade: string | null }> {
    const enabled = await this.flags.resolve(
      tenantId,
      'academic.grading_matrix',
    );
    if (!enabled || caScore == null || examScore == null) {
      const total =
        caScore != null && examScore != null
          ? Number(caScore) + Number(examScore)
          : null;
      return {
        total,
        grade: total != null ? letter(total, DEFAULT_MATRIX.gradeBands) : null,
      };
    }
    let matrix = DEFAULT_MATRIX;
    try {
      matrix = await this.get(tenantId);
    } catch {
      matrix = DEFAULT_MATRIX;
    }
    const total =
      (Number(caScore) * matrix.caWeight) / 100 +
      (Number(examScore) * matrix.examWeight) / 100;
    return {
      total: Math.round(total * 100) / 100,
      grade: letter(total, matrix.gradeBands),
    };
  }
}

export function validateGradingBands(bands: GradeBand[]): void {
  if (!Array.isArray(bands) || bands.length === 0) {
    throw new BadRequestException('At least one grading band is required');
  }
  const normalized = bands.map((value: unknown) => {
    const band =
      value && typeof value === 'object'
        ? (value as Record<string, unknown>)
        : {};
    return {
      grade_title: String(band.grade_title ?? ''),
      grade_name: String(band.grade_name ?? ''),
      min: Number(band.min),
      max: Number(band.max),
    };
  });
  if (
    normalized.some(
      (band) =>
        !Number.isFinite(band.min) ||
        !Number.isFinite(band.max) ||
        band.min < 0 ||
        band.max > 100 ||
        band.min > band.max,
    )
  ) {
    throw new BadRequestException(
      'Grade band ranges must be between 0 and 100',
    );
  }
  const mins = normalized.map((band) => band.min);
  if (new Set(mins).size !== mins.length) {
    throw new BadRequestException('Grade band minimums must be unique');
  }
  if (
    normalized.some(
      (band) =>
        !String(band.grade_title || '').trim() ||
        !String(band.grade_name || '').trim(),
    )
  ) {
    throw new BadRequestException('Every grading band needs a title and name');
  }
  const sorted = [...normalized].sort((a, b) => a.min - b.min);
  for (let index = 1; index < sorted.length; index += 1) {
    if (sorted[index].min <= sorted[index - 1].max) {
      throw new BadRequestException('Grade band ranges must not overlap');
    }
  }
}

function parseBands(raw: unknown) {
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw);
    } catch {
      return DEFAULT_MATRIX.gradeBands;
    }
  }
  if (!Array.isArray(raw)) return DEFAULT_GRADE_BANDS;
  const parsed = raw.map((value: unknown) => {
    const band =
      value && typeof value === 'object'
        ? (value as Record<string, unknown>)
        : {};
    return {
      grade_title: String(band.grade_title ?? band.grade ?? ''),
      grade_name: String(
        band.grade_name ?? band.grade_title ?? band.grade ?? '',
      ),
      min: Number(band.min),
      max: Number.isFinite(Number(band.max)) ? Number(band.max) : null,
    };
  });
  const sorted = [...parsed].sort((a, b) => a.min - b.min);
  return sorted.map((band, index) => ({
    ...band,
    max: band.max ?? (sorted[index + 1] ? sorted[index + 1].min - 1 : 100),
  }));
}

function letter(score: number, bands: GradeBand[]) {
  for (const band of bands) {
    if (score >= band.min && score <= band.max) return band.grade_title;
  }
  return bands.find((band) => band.grade_title === 'F')?.grade_title || null;
}
