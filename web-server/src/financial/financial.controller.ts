import { Body, Controller, Delete, Get, Param, Patch, Post } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { FinancialService } from './financial.service';
import {
  CreateFeeStructureDto,
  CreateInvoiceDto,
  CreatePaymentDto,
  CreateRefundDto,
  CreateScholarshipDto,
  UpdateFeeStructureDto,
  UpdateInvoiceDto,
  UpdateScholarshipDto,
} from './dto/financial.dto';
import { RequireFeature } from '../platform-config/require-feature.decorator';

@Controller('financial')
@RequireFeature('fees.structures', 'fees.invoices', 'fees.payments_manual')
export class FinancialController {
  constructor(private readonly financialService: FinancialService) {}

  @Get('fee-structures')
  @RequirePermissions('fee_structures:read', 'fees:manage')
  async listFees(@TenantId() tenantId: string) {
    return ok('Fee structures', await this.financialService.listFeeStructures(tenantId));
  }

  @Post('fee-structures')
  @RequirePermissions('fee_structures:create', 'fees:manage')
  async createFee(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateFeeStructureDto,
  ) {
    return ok(
      'Fee structure created',
      await this.financialService.createFeeStructure(tenantId, body, user.user_id),
    );
  }

  @Patch('fee-structures/:id')
  @RequirePermissions('fee_structures:update', 'fees:manage')
  async updateFee(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: UpdateFeeStructureDto,
  ) {
    return ok('Fee structure updated', await this.financialService.updateFeeStructure(tenantId, id, body));
  }

  @Delete('fee-structures/:id')
  @RequirePermissions('fee_structures:delete', 'fees:manage')
  async deleteFee(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok('Fee structure deleted', await this.financialService.deleteFeeStructure(tenantId, id));
  }

  @Get('payments')
  @RequirePermissions('payments:view', 'payments:process')
  async listPayments(@TenantId() tenantId: string) {
    return ok('Payments', await this.financialService.listPayments(tenantId));
  }

  @Post('payments')
  @RequirePermissions('payments:process', 'fees:manage')
  async createPayment(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreatePaymentDto,
  ) {
    return ok(
      'Payment recorded',
      await this.financialService.createPayment(tenantId, body, { userId: user.user_id }),
    );
  }

  @Delete('payments/:id')
  @RequirePermissions('payments:process', 'fees:manage')
  async deletePayment(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok('Payment deleted', await this.financialService.deletePayment(tenantId, id));
  }

  // ─── Invoices ─────────────────────────────────────────────────────────────

  @Get('invoices')
  @RequirePermissions('invoices:read', 'fees:manage')
  async listInvoices(@TenantId() tenantId: string) {
    return ok('Invoices', await this.financialService.listInvoices(tenantId));
  }

  @Post('invoices')
  @RequirePermissions('invoices:create', 'fees:manage')
  async createInvoice(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateInvoiceDto,
  ) {
    return ok(
      'Invoice created',
      await this.financialService.createInvoice(tenantId, body, user.user_id),
    );
  }

  @Patch('invoices/:id')
  @RequirePermissions('invoices:create', 'fees:manage')
  async updateInvoice(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: UpdateInvoiceDto,
  ) {
    return ok('Invoice updated', await this.financialService.updateInvoice(tenantId, id, body));
  }

  @Delete('invoices/:id')
  @RequirePermissions('invoices:create', 'fees:manage')
  async deleteInvoice(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok('Invoice deleted', await this.financialService.deleteInvoice(tenantId, id));
  }

  @Get('invoices/:id/payment-proof')
  @RequirePermissions('invoices:read', 'fees:manage')
  async invoicePaymentProof(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok(
      'Invoice payment proof',
      await this.financialService.getInvoicePaymentProof(tenantId, id),
    );
  }

  // ─── Scholarships ─────────────────────────────────────────────────────────

  @Get('scholarships')
  @RequirePermissions('scholarships:manage', 'fees:manage')
  async listScholarships(@TenantId() tenantId: string) {
    return ok('Scholarships', await this.financialService.listScholarships(tenantId));
  }

  @Post('scholarships')
  @RequirePermissions('scholarships:manage', 'fees:manage')
  async createScholarship(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateScholarshipDto,
  ) {
    return ok(
      'Scholarship created',
      await this.financialService.createScholarship(tenantId, body, user.user_id),
    );
  }

  @Patch('scholarships/:id')
  @RequirePermissions('scholarships:manage', 'fees:manage')
  async updateScholarship(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: UpdateScholarshipDto,
  ) {
    return ok(
      'Scholarship updated',
      await this.financialService.updateScholarship(tenantId, id, body),
    );
  }

  @Delete('scholarships/:id')
  @RequirePermissions('scholarships:manage', 'fees:manage')
  async deleteScholarship(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok('Scholarship deleted', await this.financialService.deleteScholarship(tenantId, id));
  }

  // ─── Refunds ──────────────────────────────────────────────────────────────

  @Get('refunds')
  @RequirePermissions('refunds:process', 'fees:manage')
  async listRefunds(@TenantId() tenantId: string) {
    return ok('Refunds', await this.financialService.listRefunds(tenantId));
  }

  @Post('refunds')
  @RequirePermissions('refunds:process', 'fees:manage')
  async createRefund(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateRefundDto,
  ) {
    return ok(
      'Refund processed',
      await this.financialService.createRefund(tenantId, body, user.user_id),
    );
  }

  @Get('students/:studentId/history')
  @RequirePermissions('payments:view', 'invoices:read', 'fees:manage')
  async studentHistory(
    @TenantId() tenantId: string,
    @Param('studentId') studentId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Student financial history',
      await this.financialService.getStudentHistory(
        tenantId,
        studentId,
        user.user_id || user.sub,
      ),
    );
  }
}
