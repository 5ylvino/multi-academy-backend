import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { RuntimeConfigService } from '../platform-config/runtime-config.service';

/**
 * Phase 3 stubs: split settlement (school + SaaS fee) and bank reconciliation.
 */
@Injectable()
export class FeesOpsService {
  private readonly logger = new Logger(FeesOpsService.name);

  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
    private readonly runtimeConfig: RuntimeConfigService,
  ) {}

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensure(ds: any) {
    await ds.query(`
      CREATE TABLE IF NOT EXISTS fee_split_settlements (
        id varchar(64) PRIMARY KEY,
        payment_reference varchar(128) NOT NULL,
        gross_minor int NOT NULL,
        school_share_minor int NOT NULL,
        saas_share_minor int NOT NULL,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        status varchar(32) NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
    await ds.query(`
      CREATE TABLE IF NOT EXISTS bank_recon_entries (
        id varchar(64) PRIMARY KEY,
        bank_reference varchar(128) NOT NULL,
        amount_minor int NOT NULL,
        currency varchar(8) NOT NULL DEFAULT 'NGN',
        matched_payment_ref varchar(128) NULL,
        status varchar(32) NOT NULL DEFAULT 'unmatched',
        notes text NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
    `);
  }

  async listSplits(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'fees.split_settlement');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    return runDbQuery(
      ds,
      `SELECT id, payment_reference as "paymentReference", gross_minor as "grossMinor",
              school_share_minor as "schoolShareMinor", saas_share_minor as "saasShareMinor",
              currency, status, created_at as "createdAt"
       FROM fee_split_settlements ORDER BY created_at DESC LIMIT 200`,
      [],
    );
  }

  /**
   * Idempotently record a fee split after a successful gateway school-fee settlement.
   * Uses control-plane `feeSplits` rules; skips silently when the feature is off or no rule matches.
   */
  async maybeRecordAutoSplit(
    tenantId: string,
    input: {
      paymentReference: string;
      grossMinor: number;
      currency: string;
      providerId: string;
      sessionKind: string;
    },
  ): Promise<void> {
    try {
      if (input.sessionKind === 'saas_subscription') return;
      if (!(await this.flags.resolve(tenantId, 'fees.split_settlement'))) return;

      const config = await this.runtimeConfig.getConfig(tenantId);
      const rules = config.feeSplits || [];
      const feeType = 'school_fees';
      const rule =
        rules.find((r) => r.feeType === feeType && r.providerId === input.providerId) ||
        rules.find((r) => r.feeType === feeType);
      if (!rule?.allocations?.length) return;

      const platformAlloc = rule.allocations.find((a) => a.destination === 'platform');
      const bps = Number(platformAlloc?.percentage_bps);
      if (!Number.isFinite(bps) || bps <= 0) return;

      const ds = await this.getTenantDs(tenantId);
      await this.ensure(ds);

      const existing = await runDbQuery(
        ds,
        `SELECT id FROM fee_split_settlements WHERE payment_reference = ? LIMIT 1`,
        [input.paymentReference],
      );
      if (existing.length) return;

      const saas = Math.round((input.grossMinor * bps) / 10_000);
      const school = input.grossMinor - saas;
      const id = randomToken('split');
      await runDbQuery(
        ds,
        `INSERT INTO fee_split_settlements
           (id, payment_reference, gross_minor, school_share_minor, saas_share_minor, currency, status)
         VALUES (?, ?, ?, ?, ?, ?, 'pending')`,
        [id, input.paymentReference, input.grossMinor, school, saas, input.currency || 'NGN'],
      );
    } catch (err) {
      this.logger.warn(
        `Auto fee split skipped for ${input.paymentReference}: ${
          err instanceof Error ? err.message : err
        }`,
      );
    }
  }

  async createSplit(
    tenantId: string,
    body: {
      paymentReference: string;
      grossMinor: number;
      saasShareBps?: number;
      currency?: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'fees.split_settlement');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const bps = body.saasShareBps ?? 250; // 2.5% default stub
    const saas = Math.round((body.grossMinor * bps) / 10_000);
    const school = body.grossMinor - saas;
    const id = randomToken('split');
    await runDbQuery(
      ds,
      `INSERT INTO fee_split_settlements
         (id, payment_reference, gross_minor, school_share_minor, saas_share_minor, currency, status)
       VALUES (?, ?, ?, ?, ?, ?, 'pending')`,
      [id, body.paymentReference, body.grossMinor, school, saas, body.currency || 'NGN'],
    );
    return {
      id,
      paymentReference: body.paymentReference,
      grossMinor: body.grossMinor,
      schoolShareMinor: school,
      saasShareMinor: saas,
      saasShareBps: bps,
      currency: body.currency || 'NGN',
      status: 'pending',
      note: 'Settlement recorded — execute gateway split payout via provider adapter when live',
    };
  }

  async listRecon(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'fees.bank_reconciliation');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    return runDbQuery(
      ds,
      `SELECT id, bank_reference as "bankReference", amount_minor as "amountMinor",
              currency, matched_payment_ref as "matchedPaymentRef", status, notes,
              created_at as "createdAt"
       FROM bank_recon_entries ORDER BY created_at DESC LIMIT 200`,
      [],
    );
  }

  async ingestBankRow(
    tenantId: string,
    body: {
      bankReference: string;
      amountMinor: number;
      currency?: string;
      matchedPaymentRef?: string;
      notes?: string;
    },
  ) {
    await this.flags.assertEnabled(tenantId, 'fees.bank_reconciliation');
    const ds = await this.getTenantDs(tenantId);
    await this.ensure(ds);
    const id = randomToken('recon');
    const status = body.matchedPaymentRef ? 'matched' : 'unmatched';
    await runDbQuery(
      ds,
      `INSERT INTO bank_recon_entries
         (id, bank_reference, amount_minor, currency, matched_payment_ref, status, notes)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
      [
        id,
        body.bankReference,
        body.amountMinor,
        body.currency || 'NGN',
        body.matchedPaymentRef || null,
        status,
        body.notes || 'Stub bank feed row',
      ],
    );
    return { id, status };
  }
}
