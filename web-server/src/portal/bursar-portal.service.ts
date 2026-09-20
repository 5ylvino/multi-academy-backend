import { Injectable, ForbiddenException } from '@nestjs/common';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { FinancialService } from '../financial/financial.service';
import { BalanceFreezeService } from '../financial/balance-freeze.service';
import { PortalReadServiceClient } from './portal-read-service.client';

/** Bursar-focused finance summary — gated by portal.bursar. */
@Injectable()
export class BursarPortalService {
  constructor(
    private readonly flags: FeatureFlagService,
    private readonly financial: FinancialService,
    private readonly freeze: BalanceFreezeService,
    private readonly portalRead: PortalReadServiceClient,
  ) {}

  async getDashboard(tenantId: string) {
    if (this.portalRead.isEnabled()) {
      return this.portalRead.get(
        'bursar/dashboard',
        tenantId,
        { userId: 'bursar', roles: ['bursar', 'admin'] },
        ['portal.bursar'],
      );
    }
    return this.getDashboardLocal(tenantId);
  }

  async getDashboardLocal(tenantId: string) {
    await this.flags.assertEnabled(tenantId, 'portal.bursar');

    const [structures, invoices, payments, scholarships, refunds] = await Promise.all([
      this.financial.listFeeStructures(tenantId).catch(() => []),
      this.financial.listInvoices(tenantId).catch(() => []),
      this.financial.listPayments(tenantId).catch(() => []),
      this.financial.listScholarships(tenantId).catch(() => []),
      this.financial.listRefunds(tenantId).catch(() => []),
    ]);

    const inv = Array.isArray(invoices) ? invoices : [];
    const pays = Array.isArray(payments) ? payments : [];
    const openInvoices = inv.filter(
      (i: any) => !['paid', 'void', 'cancelled'].includes(String(i.status || '').toLowerCase()),
    );
    const collected = pays.reduce(
      (s: number, p: any) => s + Number(p.amount ?? p.amountMinor ?? 0),
      0,
    );
    const outstanding = openInvoices.reduce(
      (s: number, i: any) => s + Number(i.balance ?? i.amount ?? i.amountMinor ?? 0),
      0,
    );

    const freezeOn = await this.flags.resolve(tenantId, 'fees.balance_freeze');
    const gatewayOn = await this.flags.resolve(tenantId, 'fees.gateway');
    const installmentsOn = await this.flags.resolve(tenantId, 'fees.installments');
    const advanceOn = await this.flags.resolve(tenantId, 'fees.advance_payment');
    const splitOn = await this.flags.resolve(tenantId, 'fees.split_settlement');
    const reconOn = await this.flags.resolve(tenantId, 'fees.bank_reconciliation');

    return {
      summary: {
        feeStructures: Array.isArray(structures) ? structures.length : 0,
        invoices: inv.length,
        openInvoices: openInvoices.length,
        payments: pays.length,
        collected,
        outstanding,
        scholarships: Array.isArray(scholarships) ? scholarships.length : 0,
        refunds: Array.isArray(refunds) ? refunds.length : 0,
      },
      recentPayments: pays.slice(0, 15),
      openInvoices: openInvoices.slice(0, 15),
      tools: {
        balanceFreeze: freezeOn,
        gateway: gatewayOn,
        installments: installmentsOn,
        advancePayment: advanceOn,
        splitSettlement: splitOn,
        bankReconciliation: reconOn,
      },
    };
  }

  async assertBursarPortal(tenantId: string) {
    const on = await this.flags.resolve(tenantId, 'portal.bursar');
    if (!on) throw new ForbiddenException('Feature disabled: portal.bursar');
  }
}
