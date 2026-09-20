import {
  BadRequestException,
  Body,
  Controller,
  Delete,
  Get,
  NotFoundException,
  Param,
  Patch,
  Post,
  Res,
} from '@nestjs/common';
import type { Response } from 'express';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { Public } from '../common/auth/public.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { OrganizationsService } from './organizations.service';
import { GatewayPaymentsService } from '../financial/gateway-payments.service';
import {
  ChangePlanDto,
  CreateOrganizationDto,
  UpdateOrganizationDto,
  CreateSaasCheckoutDto,
} from './dto/organization.dto';
import { VerifyGatewayPaymentDto } from '../financial/dto/gateway-payments.dto';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { isValidSchoolSlug } from '../common/utils/id.util';

@Controller('organizations')
@RequireFeature('org.profile', 'org.branding', 'org.subscription_view')
export class OrganizationsController {
  constructor(
    private readonly organizationsService: OrganizationsService,
    private readonly gatewayPayments: GatewayPaymentsService,
  ) {}

  @Post()
  @RequirePermissions('organization:update', 'organization:manage')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateOrganizationDto,
  ) {
    const result = await this.organizationsService.createBusinessOrganization({
      tenantId,
      userId: user.user_id,
      body,
    });
    return ok('Business organization saved', result);
  }

  @Get()
  @RequirePermissions('organization:view', 'organization:manage')
  async list(@TenantId() tenantId: string) {
    const rows = await this.organizationsService.listBusinessOrganizations(
      tenantId,
    );
    return ok('Organizations', rows);
  }

  @Get('current')
  async current(@TenantId() tenantId: string) {
    const org = await this.organizationsService.getCurrentOrganization(
      tenantId,
    );
    return ok('Current organization', org);
  }

  @Get('overview')
  @RequirePermissions('organization:view', 'organization:manage')
  async overview(@TenantId() tenantId: string) {
    const data = await this.organizationsService.getOrganizationOverview(
      tenantId,
    );
    return ok('Organization overview', data);
  }

  /** Public branded login config for /{slug}/login (no auth). */
  @Public()
  @Get('public/login/:slug')
  async publicLogin(@Param('slug') slug: string) {
    if (!isValidSchoolSlug(slug)) {
      throw new NotFoundException(
        'Custom login page not found for this school',
      );
    }
    const data = await this.organizationsService.getPublicLoginBrandingBySlug(
      slug,
    );
    return ok('Public login branding', data);
  }

  @Get('subscription/current')
  @RequirePermissions('organization:view', 'organization:manage')
  async currentSubscription(@TenantId() tenantId: string) {
    const sub = await this.organizationsService.getCurrentSubscription(
      tenantId,
    );
    return ok('Current subscription', sub);
  }

  @Get('subscription/plans')
  @RequirePermissions('organization:view', 'organization:manage')
  async subscriptionPlans() {
    const plans = await this.organizationsService.listCatalogPlans();
    return ok('Subscription plans', plans);
  }

  @Post('subscription/change-plan')
  @RequirePermissions('organization:update', 'organization:manage')
  async changePlan(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: ChangePlanDto,
  ) {
    const sub = await this.organizationsService.changeSubscription({
      tenantId,
      userId: user.user_id,
      planId: body.planId,
      billingCycle: body.billingCycle,
    });
    if (sub.pendingInvoiceId) {
      const invoice = (
        await this.organizationsService.listInvoices(tenantId)
      ).find((item: any) => String(item.id) === String(sub.pendingInvoiceId));
      if (!invoice) {
        throw new NotFoundException('Subscription invoice not found');
      }
      const checkout = await this.gatewayPayments.createCheckout(
        tenantId,
        {
          studentId: `saas-${invoice.id}`,
          invoiceId: String(invoice.id),
          amount:
            Number(invoice.amount) - Number(invoice.amountPaid || 0) / 100,
          email: user.email,
          callbackUrl:
            body.callbackUrl ||
            'http://localhost:3000/dashboard/organization/subscription/invoices',
          kind: 'saas_subscription',
          sessionLabel: `SaaS plan change ${invoice.invoiceNo}`,
        },
        user.user_id,
      );
      return ok('Payment required to activate subscription', {
        ...sub,
        checkoutUrl: checkout.checkoutUrl,
        paymentReference: checkout.reference,
      });
    }
    return ok('Subscription updated', sub);
  }

  @Get('subscription/invoices')
  @RequirePermissions('organization:view', 'organization:manage')
  async invoices(@TenantId() tenantId: string) {
    const invoices = await this.organizationsService.listInvoices(tenantId);
    return ok('Subscription invoices', invoices);
  }

  @Get('subscription/invoices/:id/pdf')
  @RequirePermissions('organization:view', 'organization:manage')
  async invoicePdf(
    @TenantId() tenantId: string,
    @Param('id') invoiceId: string,
    @Res() response: Response,
  ) {
    const pdf = await this.organizationsService.downloadInvoicePdf(
      tenantId,
      Number(invoiceId),
    );
    response.setHeader('Content-Type', pdf.contentType);
    if (pdf.contentDisposition) {
      response.setHeader('Content-Disposition', pdf.contentDisposition);
    }
    response.send(pdf.body);
  }

  @Post('subscription/invoices/:id/checkout')
  @RequirePermissions('organization:update', 'organization:manage')
  async invoiceCheckout(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') invoiceId: string,
    @Body() body: CreateSaasCheckoutDto,
  ) {
    const invoice = (
      await this.organizationsService.listInvoices(tenantId)
    ).find((item: any) => String(item.id) === invoiceId);
    if (!invoice) {
      throw new NotFoundException('Subscription invoice not found');
    }
    if (invoice.status === 'paid') {
      throw new BadRequestException('Subscription invoice is already paid');
    }
    return ok(
      'Subscription checkout created',
      await this.gatewayPayments.createCheckout(
        tenantId,
        {
          studentId: `saas-${invoiceId}`,
          invoiceId,
          amount:
            Number(invoice.amount) - Number(invoice.amountPaid || 0) / 100,
          email: body.email,
          callbackUrl: body.callbackUrl,
          kind: 'saas_subscription',
          sessionLabel: `SaaS invoice ${invoice.invoiceNo}`,
        },
        user.user_id,
      ),
    );
  }

  @Post('subscription/invoices/checkout/verify')
  @RequirePermissions('organization:update', 'organization:manage')
  async verifyInvoiceCheckout(
    @TenantId() tenantId: string,
    @Body() body: VerifyGatewayPaymentDto,
  ) {
    return ok(
      'Subscription payment verified',
      await this.gatewayPayments.verify(tenantId, body.reference),
    );
  }

  @Get(':id')
  @RequirePermissions('organization:view', 'organization:manage')
  async getOne(@TenantId() tenantId: string, @Param('id') id: string) {
    const row = await this.organizationsService.getBusinessOrganizationById(
      tenantId,
      id,
    );
    return ok('Organization', row);
  }

  @Patch(':id')
  @RequirePermissions('organization:update', 'organization:manage')
  async update(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: UpdateOrganizationDto,
  ) {
    const row = await this.organizationsService.updateBusinessOrganization({
      tenantId,
      userId: user.user_id,
      id,
      body,
    });
    return ok('Organization updated', row);
  }

  @Delete(':id')
  @RequirePermissions('organization:update', 'organization:manage')
  async remove(@TenantId() tenantId: string, @Param('id') id: string) {
    const res = await this.organizationsService.deleteBusinessOrganization(
      tenantId,
      id,
    );
    return ok('Organization deleted', res);
  }
}
