import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { ControlPlaneService } from '../control-plane/control-plane.service';
import { TenantConnectionService } from '../database/tenant-connection.service';
import { randomToken } from '../common/utils/id.util';
import { runDbQuery } from '../database/db-driver.util';
import { FeatureFlagService } from '../platform-config/feature-flag.service';
import { ProviderRegistryService } from '../platform-config/providers/provider-registry.service';
import { ProviderSecretsService } from '../platform-config/provider-secrets.service';
import { PaystackGateway } from '../platform-config/providers/paystack.gateway';
import { CommsService } from '../platform-config/comms.service';
import { NotificationsService } from '../notifications/notifications.service';
import { GATEWAY_PAYMENT_TABLES, ensureFromMigration } from '../database/ensure-tenant-tables.util';
import { RealtimeService } from '../realtime/realtime.service';
import { ControlApiClient } from '../platform-config/control-api.client';
import { PaymentServiceClient } from '../payments/payment-service.client';
import { FeesOpsService } from '../fees-ops/fees-ops.service';

@Injectable()
export class GatewayPaymentsService {
  private readonly logger = new Logger(GatewayPaymentsService.name);

  constructor(
    private readonly controlPlane: ControlPlaneService,
    private readonly tenantConnections: TenantConnectionService,
    private readonly flags: FeatureFlagService,
    private readonly providers: ProviderRegistryService,
    private readonly secrets: ProviderSecretsService,
    private readonly comms: CommsService,
    private readonly notifications: NotificationsService,
    private readonly controlApi: ControlApiClient,
    private readonly realtime: RealtimeService,
    private readonly paymentClient: PaymentServiceClient,
    private readonly feesOps: FeesOpsService,
  ) {}

  private recordAutoSplit(
    tenantId: string,
    input: {
      paymentReference: string;
      grossMinor: number;
      currency: string;
      providerId: string;
      sessionKind: string;
    },
  ) {
    void this.feesOps.maybeRecordAutoSplit(tenantId, input).catch(() => undefined);
  }

  private async checkoutFeatures(
    tenantId: string,
    kind: string,
    hasInstallment: boolean,
  ): Promise<string[]> {
    const features: string[] = [];
    if (kind !== 'saas_subscription' && (await this.flags.resolve(tenantId, 'fees.gateway'))) {
      features.push('fees.gateway');
    }
    if (kind === 'advance' && (await this.flags.resolve(tenantId, 'fees.advance_payment'))) {
      features.push('fees.advance_payment');
    }
    if (
      (kind === 'installment' || hasInstallment) &&
      (await this.flags.resolve(tenantId, 'fees.installments'))
    ) {
      features.push('fees.installments');
    }
    return features;
  }

  private async getTenantDs(tenantId: string) {
    const tenant = await this.controlPlane.getTenantById(tenantId);
    if (!tenant) throw new NotFoundException('Tenant not found');
    return this.tenantConnections.getOrCreate(tenant.id, tenant.dbUri);
  }

  private async ensureGatewayTables(ds: any) {
    await ensureFromMigration(ds, GATEWAY_PAYMENT_TABLES);
  }

  async createCheckout(
    tenantId: string,
    body: {
      studentId: string;
      invoiceId?: string;
      installmentId?: string;
      amount: number;
      email: string;
      callbackUrl: string;
      currency?: string;
      kind?: 'invoice' | 'advance' | 'installment' | 'saas_subscription';
      term?: string;
      sessionLabel?: string;
    },
    createdBy?: string,
  ) {
    if (body.kind !== 'saas_subscription') {
      await this.flags.assertEnabled(tenantId, 'fees.gateway');
    }
    if (body.kind === 'advance') {
      await this.flags.assertEnabled(tenantId, 'fees.advance_payment');
    }
    if (body.kind === 'installment' || body.installmentId) {
      await this.flags.assertEnabled(tenantId, 'fees.installments');
    }

    const amountMinor = Math.round(Number(body.amount) * 100);
    if (!Number.isFinite(amountMinor) || amountMinor <= 0) {
      throw new BadRequestException('Invalid amount');
    }

    const ds = await this.getTenantDs(tenantId);
    await this.ensureGatewayTables(ds);

    const kind = body.kind || 'invoice';
    if (kind === 'invoice') {
      if (!body.invoiceId) {
        throw new BadRequestException('Invoice id is required for invoice payments');
      }
      const invoices = await runDbQuery(
        ds,
        `SELECT i.student_id as "studentId", i.amount, i.status,
                COALESCE((
                  SELECT SUM(s.amount)
                  FROM financial_checkout_sessions s
                  WHERE s.invoice_id = i.id AND s.status IN ('settled', 'success')
                ), 0) as "paidAmount"
         FROM financial_invoices i
         WHERE i.id = ?
         LIMIT 1`,
        [body.invoiceId],
      );
      const invoice = invoices[0];
      if (!invoice || String(invoice.studentId) !== String(body.studentId)) {
        throw new NotFoundException('Invoice not found for student');
      }
      if (String(invoice.status).toLowerCase() === 'paid') {
        throw new BadRequestException('Invoice is already paid');
      }
      const outstanding = Number(invoice.amount) - Number(invoice.paidAmount || 0);
      if (!Number.isFinite(outstanding) || outstanding <= 0) {
        throw new BadRequestException('Invoice is already fully paid');
      }
      if (Number(body.amount) > outstanding) {
        throw new BadRequestException('Payment exceeds invoice balance');
      }
    }
    if (body.installmentId) {
      const installmentRows = await runDbQuery(
        ds,
        `SELECT i.amount, i.status, p.student_id as "studentId", p.invoice_id as "invoiceId"
         FROM financial_installments i
         JOIN financial_installment_plans p ON p.id = i.plan_id
         WHERE i.id = ? LIMIT 1`,
        [body.installmentId],
      );
      const installment = installmentRows[0];
      if (
        !installment ||
        String(installment.studentId) !== String(body.studentId) ||
        (body.invoiceId && String(installment.invoiceId) !== String(body.invoiceId))
      ) {
        throw new NotFoundException('Installment not found for student');
      }
      if (String(installment.status).toLowerCase() !== 'pending') {
        throw new BadRequestException('Installment is no longer payable');
      }
      if (Math.abs(Number(body.amount) - Number(installment.amount)) > 0.01) {
        throw new BadRequestException('Payment amount does not match the installment');
      }
    }

    const currency = (body.currency || 'NGN').toUpperCase();
    const reference = `pay_${tenantId.slice(0, 8)}_${randomToken('ref').slice(0, 16)}`;
    const sessionId = randomToken('chk');
    const checkoutMetadata = {
      tenantId,
      studentId: body.studentId,
      invoiceId: body.invoiceId,
      installmentId: body.installmentId,
      kind,
      sessionId,
      term: body.term,
      sessionLabel: body.sessionLabel,
      email: body.email,
    };

    let providerId: string;
    let checkoutUrl: string;
    let accessCode: string | undefined;

    if (this.paymentClient.isEnabled()) {
      const features = await this.checkoutFeatures(
        tenantId,
        kind,
        Boolean(body.installmentId),
      );
      const remote = await this.paymentClient.createCheckout(
        tenantId,
        { userId: createdBy || 'system' },
        {
          context: PaymentServiceClient.kindToContext(kind),
          amountMinor,
          email: body.email,
          callbackUrl: body.callbackUrl,
          currency,
          reference,
          metadata: checkoutMetadata,
          createdBy: createdBy || undefined,
        },
        features,
      );
      providerId = remote.gatewayId;
      checkoutUrl = remote.checkoutUrl;
      accessCode = remote.accessCode;
    } else {
      const gateway = await this.providers.resolvePayment(tenantId);
      if (gateway.id === 'disabled') {
        throw new ForbiddenException('Payment gateway unavailable');
      }
      providerId = gateway.id;
      const checkout = await gateway.createCheckout({
        amountMinor,
        currency,
        reference,
        email: body.email,
        callbackUrl: body.callbackUrl,
        metadata: checkoutMetadata,
      });
      checkoutUrl = checkout.checkoutUrl;
      accessCode = checkout.accessCode;
    }

    await runDbQuery(
      ds,
      `INSERT INTO financial_checkout_sessions
        (id, student_id, invoice_id, installment_id, amount, currency, reference, provider_id, status, kind, metadata, created_by, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, NOW())`,
      [
        sessionId,
        body.studentId,
        body.invoiceId || null,
        body.installmentId || null,
        body.amount,
        currency,
        reference,
        providerId,
        kind,
        JSON.stringify({
          term: body.term,
          sessionLabel: body.sessionLabel,
          email: body.email,
        }),
        createdBy || null,
      ],
    );

    if (this.controlApi.isConfigured()) {
      void this.controlApi.postUsage({
        tenantId,
        meterKey: 'gateway_checkouts',
        quantity: 1,
        eventId: reference,
      });
    }

    return {
      sessionId,
      reference,
      providerId,
      checkoutUrl,
      accessCode,
    };
  }

  async verify(
    tenantId: string,
    reference: string,
  ): Promise<{ status: string; paymentId?: string }> {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureGatewayTables(ds);
    const sessions = await runDbQuery(
      ds,
      `SELECT * FROM financial_checkout_sessions WHERE reference = ? LIMIT 1`,
      [reference],
    );
    const session = sessions[0];
    const isSaasSubscription = session?.kind === 'saas_subscription';
    if (!isSaasSubscription) {
      await this.flags.assertEnabled(tenantId, 'fees.gateway');
    }

    let verification: {
      status: string;
      amountMinor: number;
      currency: string;
      providerReference?: string;
      providerId?: string;
    };

    if (this.paymentClient.isEnabled()) {
      const features = await this.checkoutFeatures(
        tenantId,
        String(session?.kind || 'invoice'),
        Boolean(session?.installment_id),
      );
      const remote = await this.paymentClient.verify(
        tenantId,
        { userId: 'system' },
        reference,
        features,
      );
      verification = {
        status: remote.status,
        amountMinor: remote.amountMinor,
        currency: remote.currency,
        providerReference: remote.providerReference || reference,
        providerId: remote.gatewayId,
      };
    } else {
      const gateway = await this.providers.resolvePayment(tenantId);
      if (gateway.id === 'disabled') {
        throw new ForbiddenException('Payment gateway unavailable');
      }
      const local = await gateway.verifyTransaction({
        reference,
        tenantId: isSaasSubscription ? '__platform__' : tenantId,
      });
      verification = {
        status: local.status,
        amountMinor: local.amountMinor,
        currency: local.currency,
        providerReference: local.providerReference || reference,
        providerId: gateway.id,
      };
    }

    if (verification.status !== 'success') {
      return { status: verification.status };
    }
    if (isSaasSubscription) {
      const invoiceId = Number(session.invoice_id);
      if (!Number.isInteger(invoiceId) || invoiceId <= 0) {
        throw new BadRequestException('SaaS invoice reference is invalid');
      }
      await this.controlApi.settleTenantInvoice(tenantId, invoiceId, {
        amountMinor: verification.amountMinor,
        providerReference: verification.providerReference || reference,
        providerId: verification.providerId || session.provider_id,
      });
      await runDbQuery(
        ds,
        `UPDATE financial_checkout_sessions
         SET status = 'settled', provider_reference = ?, settled_at = NOW()
         WHERE id = ?`,
        [verification.providerReference || reference, session.id],
      );
      return { status: 'success', paymentId: `saas-invoice-${invoiceId}` };
    }
    const paymentId = await this.settleSuccess(tenantId, {
      reference,
      amountMinor: verification.amountMinor,
      currency: verification.currency,
      providerId: verification.providerId || session.provider_id,
      providerReference: verification.providerReference || reference,
    });
    return { status: 'success', paymentId };
  }

  async settleFromWebhook(
    providerId: string,
    headers: Record<string, string>,
    rawBody: Buffer,
  ): Promise<{ status: string; tenantId?: string }> {
    let payload: any;
    try {
      payload = JSON.parse(rawBody.toString('utf8'));
    } catch {
      throw new BadRequestException('Invalid webhook body');
    }

    const tenantId = String(
      payload?.data?.metadata?.tenantId || payload?.data?.metadata?.tenant_id || '',
    );
    if (!tenantId) {
      this.logger.warn('Webhook missing tenantId metadata');
      return { status: 'ignored' };
    }

    const secretScope =
      payload?.data?.metadata?.kind === 'saas_subscription' ? '__platform__' : tenantId;

    if (providerId === 'paystack') {
      const vault = await this.secrets.getSecrets(secretScope, 'payment');
      const secret =
        vault?.secrets?.secret_key ||
        vault?.secrets?.secretKey ||
        vault?.secrets?.SECRET_KEY;
      const sig =
        headers['x-paystack-signature'] ||
        headers['X-Paystack-Signature'] ||
        '';
      if (!secret || !PaystackGateway.verifySignature(rawBody, sig, secret)) {
        throw new ForbiddenException('Invalid Paystack signature');
      }
    }

    if (providerId === 'flutterwave') {
      const vault = await this.secrets.getSecrets(secretScope, 'payment');
      const secret =
        vault?.secrets?.secret_hash ||
        vault?.secrets?.secretHash ||
        vault?.secrets?.webhook_secret ||
        vault?.secrets?.secret_key ||
        process.env.FLUTTERWAVE_WEBHOOK_SECRET ||
        '';
      const signature = headers['verif-hash'] || headers['Verif-Hash'] || '';
      // Fail closed: a missing verif-hash used to skip verification entirely,
      // so anyone who found the endpoint could forge payment confirmations.
      if (!secret) {
        throw new ForbiddenException(
          'Flutterwave webhook secret is not configured in the control vault',
        );
      }
      if (!signature || signature !== secret) {
        throw new ForbiddenException('Invalid Flutterwave signature');
      }
    }

    const gateway = await this.providers.resolvePayment(tenantId);
    if (gateway.id !== providerId) {
      return { status: 'ignored', tenantId };
    }

    const result = await gateway.handleWebhook(headers, rawBody);
    if (result.status !== 'success' || !result.reference) {
      return { status: result.status, tenantId };
    }

    await this.settleSuccess(tenantId, {
      reference: result.reference,
      amountMinor: result.amountMinor || 0,
      currency: result.currency || 'NGN',
      providerId,
      providerReference: result.reference,
    });
    return { status: 'success', tenantId };
  }

  private async settleSuccess(
    tenantId: string,
    input: {
      reference: string;
      amountMinor: number;
      currency: string;
      providerId: string;
      providerReference: string;
    },
  ): Promise<string> {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureGatewayTables(ds);

    const sessions = await runDbQuery(
      ds,
      `SELECT * FROM financial_checkout_sessions WHERE reference = ? LIMIT 1`,
      [input.reference],
    );
    if (!sessions.length) {
      throw new NotFoundException('Checkout session not found');
    }
    const session = sessions[0];
    if (session.status === 'success' || session.status === 'settled') {
      return session.id;
    }

    const expectedAmountMinor = Math.round(Number(session.amount) * 100);
    const receivedAmountMinor = Math.round(Number(input.amountMinor));
    if (
      !Number.isFinite(receivedAmountMinor) ||
      receivedAmountMinor <= 0 ||
      receivedAmountMinor !== expectedAmountMinor
    ) {
      this.logger.warn(
        `Rejecting payment settlement amount mismatch for ${input.reference}: expected ${expectedAmountMinor}, received ${receivedAmountMinor}`,
      );
      throw new BadRequestException('Payment amount does not match checkout session');
    }
    const expectedCurrency = String(session.currency || 'NGN').toUpperCase();
    if (String(input.currency || '').toUpperCase() !== expectedCurrency) {
      throw new BadRequestException('Payment currency does not match checkout session');
    }

    if (session.kind === 'saas_subscription') {
      const invoiceId = Number(session.invoice_id);
      if (!Number.isInteger(invoiceId) || invoiceId <= 0) {
        throw new BadRequestException('SaaS invoice reference is invalid');
      }
      await this.controlApi.settleTenantInvoice(tenantId, invoiceId, {
        amountMinor: receivedAmountMinor,
        providerReference: input.providerReference,
        providerId: input.providerId,
      });
      await runDbQuery(
        ds,
        `UPDATE financial_checkout_sessions
         SET status = 'settled', provider_reference = ?, settled_at = NOW()
         WHERE id = ?`,
        [input.providerReference, session.id],
      );
      return `saas-invoice-${invoiceId}`;
    }

    // Idempotent: skip if payment with same provider_reference exists
    const existing = await runDbQuery(
      ds,
      `SELECT id FROM financial_payments WHERE provider_reference = ? LIMIT 1`,
      [input.providerReference],
    );
    if (existing.length) {
      await runDbQuery(
        ds,
        `UPDATE financial_checkout_sessions SET status = 'settled', provider_reference = ?, settled_at = NOW() WHERE id = ?`,
        [input.providerReference, session.id],
      );
      this.recordAutoSplit(tenantId, {
        paymentReference: input.reference,
        grossMinor: receivedAmountMinor,
        currency: input.currency,
        providerId: input.providerId,
        sessionKind: String(session.kind || 'invoice'),
      });
      return existing[0].id;
    }

    const amount = Number(session.amount);
    const paymentId = randomToken('pay');
    const today = new Date().toISOString().slice(0, 10);
    const receiptNumber = `RCPT-${Date.now().toString().slice(-8)}`;

    await runDbQuery(
      ds,
      `INSERT INTO financial_payments
        (id, student_id, invoice_id, installment_id, amount, method, reference, date, status, biometric_verified, recorded_by, provider_id, provider_reference, receipt_number, created_at)
       VALUES (?, ?, ?, ?, ?, 'card', ?, ?, 'completed', false, NULL, ?, ?, ?, NOW())`,
      [
        paymentId,
        session.student_id,
        session.invoice_id || null,
        session.installment_id || null,
        amount,
        input.reference,
        today,
        input.providerId,
        input.providerReference,
        receiptNumber,
      ],
    );

    await runDbQuery(
      ds,
      `UPDATE financial_checkout_sessions SET status = 'settled', provider_reference = ?, settled_at = NOW() WHERE id = ?`,
      [input.providerReference, session.id],
    );

    if (session.invoice_id) {
      const invoiceBalance = await runDbQuery(
        ds,
        `SELECT i.amount,
                COALESCE((
                  SELECT SUM(s.amount)
                  FROM financial_checkout_sessions s
                  WHERE s.invoice_id = i.id AND s.status IN ('settled', 'success')
                ), 0) as "paidAmount"
         FROM financial_invoices i
         WHERE i.id = ? LIMIT 1`,
        [session.invoice_id],
      );
      if (
        invoiceBalance.length &&
        Number(invoiceBalance[0].paidAmount) >= Number(invoiceBalance[0].amount)
      ) {
        await runDbQuery(
          ds,
          `UPDATE financial_invoices SET status = 'paid', updated_at = NOW() WHERE id = ?`,
          [session.invoice_id],
        );
      }
      await runDbQuery(
        ds,
        `UPDATE financial_fee_reminders SET status = 'stopped' WHERE invoice_id = ? AND status = 'pending'`,
        [session.invoice_id],
      );
    }

    if (session.installment_id) {
      await runDbQuery(
        ds,
        `UPDATE financial_installments SET status = 'paid', paid_at = NOW() WHERE id = ?`,
        [session.installment_id],
      );
    }

    if (session.kind === 'advance') {
      let meta: any = {};
      try {
        meta = session.metadata ? JSON.parse(session.metadata) : {};
      } catch {
        meta = {};
      }
      await runDbQuery(
        ds,
        `INSERT INTO financial_advance_credits
          (id, student_id, amount, currency, term, session_label, payment_id, reference, notes, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NOW())`,
        [
          randomToken('adv'),
          session.student_id,
          amount,
          input.currency || 'NGN',
          meta.term || null,
          meta.sessionLabel || null,
          paymentId,
          input.reference,
          'Advance / prepay fee credit',
        ],
      );
    }

    // In-app notify creator if present
    if (session.created_by) {
      try {
        await this.notifications.create({
          tenantId,
          userId: session.created_by,
          title: 'Online payment received',
          message: `Payment of ${amount} settled (ref ${input.reference}).`,
          type: 'success',
          href: '/dashboard/financial',
        });
      } catch {
        /* non-fatal */
      }
    }

    let metaEmail = '';
    try {
      const meta = session.metadata ? JSON.parse(session.metadata) : {};
      metaEmail = meta.email || '';
    } catch {
      /* ignore */
    }
    await this.comms.notifyPaymentSafe(tenantId, {
      email: metaEmail || undefined,
      amount,
      reference: input.reference,
    });

    if (this.controlApi.isConfigured()) {
      void this.controlApi.postUsage({
        tenantId,
        meterKey: 'gateway_settlements',
        quantity: 1,
        eventId: input.reference,
        properties: { paymentId, amount },
      });
    }

    try {
      this.realtime.notifyTenant(tenantId, 'payment:update', {
        paymentId,
        studentId: session.student_id,
        amount,
        reference: input.reference,
        status: 'completed',
        providerId: input.providerId,
      });
      if (session.created_by) {
        this.realtime.notifyUser(session.created_by, 'payment:update', {
          paymentId,
          amount,
          reference: input.reference,
          status: 'completed',
        });
      }
    } catch {
      /* non-fatal */
    }

    this.recordAutoSplit(tenantId, {
      paymentReference: input.reference,
      grossMinor: receivedAmountMinor,
      currency: input.currency,
      providerId: input.providerId,
      sessionKind: String(session.kind || 'invoice'),
    });

    return paymentId;
  }

  // ─── Installments ─────────────────────────────────────────────────────────

  async createInstallmentPlan(
    tenantId: string,
    body: {
      studentId: string;
      invoiceId?: string;
      title: string;
      totalAmount: number;
      surchargePercent?: number;
      installments: Array<{ amount: number; dueDate?: string }>;
    },
    createdBy?: string,
  ) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureGatewayTables(ds);
    if (!body.invoiceId) {
      throw new BadRequestException('Invoice id is required for an installment plan');
    }
    const invoiceRows = await runDbQuery(
      ds,
      `SELECT student_id as "studentId", amount, status
       FROM financial_invoices WHERE id = ? LIMIT 1`,
      [body.invoiceId],
    );
    const invoice = invoiceRows[0];
    if (!invoice || String(invoice.studentId) !== String(body.studentId)) {
      throw new NotFoundException('Invoice not found for student');
    }
    if (['paid', 'cancelled'].includes(String(invoice.status).toLowerCase())) {
      throw new BadRequestException('An installment plan cannot be created for this invoice');
    }
    const surcharge = Number(body.surchargePercent || 0);
    const expected = Number(body.totalAmount) * (1 + surcharge / 100);
    const sum = (body.installments || []).reduce(
      (acc, row) => acc + Number(row.amount || 0),
      0,
    );
    if (Math.abs(sum - expected) > 0.05) {
      throw new BadRequestException(
        'Installment amounts must equal the total including surcharge',
      );
    }
    const planId = randomToken('ipl');
    await runDbQuery(
      ds,
      `INSERT INTO financial_installment_plans
        (id, student_id, invoice_id, title, total_amount, currency, status, surcharge_percent, created_by, created_at)
       VALUES (?, ?, ?, ?, ?, 'NGN', 'active', ?, ?, NOW())`,
      [
        planId,
        body.studentId,
        body.invoiceId || null,
        body.title,
        body.totalAmount,
        surcharge,
        createdBy || null,
      ],
    );
    await runDbQuery(
      ds,
      `UPDATE financial_invoices SET payment_plan_type = 'installment', updated_at = NOW() WHERE id = ?`,
      [body.invoiceId],
    );
    let seq = 1;
    for (const row of body.installments) {
      await runDbQuery(
        ds,
        `INSERT INTO financial_installments
          (id, plan_id, sequence, amount, due_date, status, created_at)
         VALUES (?, ?, ?, ?, ?, 'pending', NOW())`,
        [randomToken('ins'), planId, seq++, row.amount, row.dueDate || null],
      );
    }
    return { id: planId };
  }

  async listInstallmentPlans(tenantId: string, studentId?: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureGatewayTables(ds);
    const plans = studentId
      ? await runDbQuery(
          ds,
          `SELECT p.id, p.student_id as "studentId", p.invoice_id as "invoiceId", p.title,
                  p.total_amount as "totalAmount", p.status, p.surcharge_percent as "surchargePercent",
                  p.created_by as "createdBy", p.created_at as "createdAt",
                  creator.name as "createdByName"
           FROM financial_installment_plans p
           LEFT JOIN users creator ON creator.id = p.created_by
           WHERE p.student_id = ? ORDER BY p.created_at DESC LIMIT 200`,
          [studentId],
        )
      : await runDbQuery(
          ds,
          `SELECT p.id, p.student_id as "studentId", p.invoice_id as "invoiceId", p.title,
                  p.total_amount as "totalAmount", p.status, p.surcharge_percent as "surchargePercent",
                  p.created_by as "createdBy", p.created_at as "createdAt",
                  creator.name as "createdByName"
           FROM financial_installment_plans p
           LEFT JOIN users creator ON creator.id = p.created_by
           ORDER BY p.created_at DESC LIMIT 200`,
        );
    const out = [];
    // Batch-load installments for the page of plans (avoids N+1 on list).
    const planIds = plans.map((p: { id: string }) => p.id);
    const installmentsByPlan = new Map<string, any[]>();
    if (planIds.length) {
      const placeholders = planIds.map(() => '?').join(',');
      const rows: any[] = await runDbQuery(
        ds,
        `SELECT id, plan_id as "planId", sequence, amount, due_date as "dueDate", status, paid_at as "paidAt"
         FROM financial_installments
         WHERE plan_id IN (${placeholders})
         ORDER BY sequence`,
        planIds,
      );
      for (const row of rows) {
        const list = installmentsByPlan.get(row.planId) || [];
        list.push({
          id: row.id,
          sequence: row.sequence,
          amount: row.amount,
          dueDate: row.dueDate,
          status: row.status,
          paidAt: row.paidAt,
        });
        installmentsByPlan.set(row.planId, list);
      }
    }
    for (const plan of plans) {
      out.push({ ...plan, installments: installmentsByPlan.get(plan.id) || [] });
    }
    return out;
  }

  async listAdvanceCredits(tenantId: string, studentId?: string) {
    await this.flags.assertEnabled(tenantId, 'fees.advance_payment');
    const ds = await this.getTenantDs(tenantId);
    await this.ensureGatewayTables(ds);
    if (studentId) {
      return runDbQuery(
        ds,
        `SELECT id, student_id as "studentId", amount, currency, term,
                session_label as "sessionLabel", payment_id as "paymentId",
                reference, notes, created_at as "createdAt"
         FROM financial_advance_credits WHERE student_id = ? ORDER BY created_at DESC LIMIT 200`,
        [studentId],
      );
    }
    return runDbQuery(
      ds,
      `SELECT id, student_id as "studentId", amount, currency, term,
              session_label as "sessionLabel", payment_id as "paymentId",
              reference, notes, created_at as "createdAt"
       FROM financial_advance_credits ORDER BY created_at DESC LIMIT 200`,
    );
  }

  async runFeeReminders(tenantId: string) {
    const ds = await this.getTenantDs(tenantId);
    await this.ensureGatewayTables(ds);
    const due: any[] = await runDbQuery(
      ds,
      `SELECT r.id, r.invoice_id as "invoiceId", r.student_id as "studentId",
              i.title, i.amount, i.status
       FROM financial_fee_reminders r
       JOIN financial_invoices i ON i.id = r.invoice_id
       WHERE r.status = 'pending'
         AND i.status IN ('unpaid', 'partial')
         AND (r.next_send_at IS NULL OR r.next_send_at <= NOW())
       LIMIT 100`,
      [],
    );
    let sent = 0;
    for (const row of due) {
      const parents = await runDbQuery(
        ds,
        `SELECT parent_id as "parentId" FROM parent_student_links WHERE student_id = ?`,
        [row.studentId],
      );
      for (const parent of parents as any[]) {
        await this.notifications.create({
          tenantId,
          userId: parent.parentId || parent.parentid,
          title: 'Fee reminder',
          message: `${row.title} of ${row.amount} is still outstanding.`,
          type: 'warning',
          href: '/dashboard/parent',
        });
      }
      await runDbQuery(
        ds,
        `UPDATE financial_fee_reminders
         SET last_sent_at = NOW(), next_send_at = NOW() + INTERVAL '3 days'
         WHERE id = ?`,
        [row.id],
      );
      sent += 1;
    }
    return { sent };
  }
}
