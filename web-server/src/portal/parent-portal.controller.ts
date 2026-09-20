import {
  Body,
  Controller,
  Get,
  Header,
  Param,
  Post,
  Query,
  Res,
  StreamableFile,
} from '@nestjs/common';
import { Response } from 'express';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { ParentPortalService } from './parent-portal.service';
import {
  IsEmail,
  IsIn,
  IsNumber,
  IsOptional,
  IsString,
  IsUrl,
  Min,
  MinLength,
} from 'class-validator';

class ParentPayDto {
  @IsString()
  @MinLength(1)
  studentId!: string;

  @IsOptional()
  @IsString()
  invoiceId?: string;

  @IsOptional()
  @IsString()
  installmentId?: string;

  @IsNumber()
  @Min(0.01)
  amount!: number;

  @IsEmail()
  email!: string;

  @IsUrl({ require_tld: false, protocols: ['http', 'https', 'masms'] })
  callbackUrl!: string;

  @IsOptional()
  @IsIn(['invoice', 'advance', 'installment'])
  kind?: 'invoice' | 'advance' | 'installment';

  @IsOptional()
  @IsString()
  term?: string;

  @IsOptional()
  @IsString()
  sessionLabel?: string;
}

class ParentPaymentProofDto {
  @IsString()
  @MinLength(1)
  invoiceId!: string;

  @IsString()
  @MinLength(1)
  fileName!: string;

  @IsString()
  @MinLength(1)
  mimeType!: string;

  @IsString()
  @MinLength(1)
  data!: string;
}

@Controller('portal/parent')
@RequireFeature('portal.parent')
export class ParentPortalController {
  constructor(private readonly portal: ParentPortalService) {}

  @Get('balance-status')
  async balanceStatus(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Balance status',
      await this.portal.getBalanceStatus(tenantId, user.user_id),
    );
  }

  @Get('home')
  async home(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok('Parent portal home', await this.portal.getHome(tenantId, user.user_id));
  }

  @Get('wards')
  async wards(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok('Wards', await this.portal.listWards(tenantId, user.user_id));
  }

  @Get('wards/:studentId/overview')
  async wardOverview(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
  ) {
    return ok(
      'Ward overview',
      await this.portal.getWardOverview(tenantId, user.user_id, studentId),
    );
  }

  @Get('wards/:studentId/fees')
  async fees(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
  ) {
    return ok(
      'Ward fees',
      await this.portal.wardFees(tenantId, user.user_id, studentId),
    );
  }

  @Get('wards/:studentId/results')
  async results(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
  ) {
    return ok(
      'Ward results',
      await this.portal.wardResults(tenantId, user.user_id, studentId),
    );
  }

  @Get('wards/:studentId/attendance-summary')
  async attendanceSummary(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
  ) {
    return ok(
      'Ward attendance summary',
      await this.portal.wardAttendanceSummary(
        tenantId,
        user.user_id,
        studentId,
      ),
    );
  }

  @Get('wards/:studentId/assignments')
  async assignments(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
  ) {
    return ok(
      'Ward assignments',
      await this.portal.wardAssignments(tenantId, user.user_id, studentId),
    );
  }

  @Get('wards/:studentId/schedule')
  async schedule(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
  ) {
    return ok(
      'Ward schedule',
      await this.portal.wardSchedule(tenantId, user.user_id, studentId),
    );
  }

  @Get('attendance-alerts')
  async attendanceAlerts(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('limit') limit?: string,
  ) {
    return ok(
      'Attendance alerts',
      await this.portal.attendanceAlerts(
        tenantId,
        user.user_id,
        limit ? Number(limit) : 50,
      ),
    );
  }

  @Get('wards/:studentId/history')
  async history(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
  ) {
    return ok(
      'Ward academic history',
      await this.portal.wardHistory(tenantId, user.user_id, studentId),
    );
  }

  @Get('wards/:studentId/report-card')
  async reportCardData(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
    @Query('termId') termId: string,
  ) {
    return ok(
      'Report card data',
      await this.portal.wardReportCardData(
        tenantId,
        user.user_id,
        studentId,
        termId,
      ),
    );
  }

  @Get('wards/:studentId/report-card/pdf')
  @Header('Content-Type', 'application/pdf')
  async reportCardPdf(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
    @Query('termId') termId: string,
    @Res({ passthrough: true }) res: Response,
  ): Promise<StreamableFile> {
    res.setHeader(
      'Content-Disposition',
      `attachment; filename="report-card-${studentId}-${termId}.pdf"`,
    );
    return this.portal.wardReportCardPdf(
      tenantId,
      user.user_id,
      studentId,
      termId,
    );
  }

  @Get('wards/:studentId/transcript/pdf')
  @Header('Content-Type', 'application/pdf')
  async transcriptPdf(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
    @Res({ passthrough: true }) res: Response,
  ): Promise<StreamableFile> {
    res.setHeader(
      'Content-Disposition',
      `attachment; filename="transcript-${studentId}.pdf"`,
    );
    return this.portal.wardTranscriptPdf(tenantId, user.user_id, studentId);
  }

  @Post('pay')
  async pay(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: ParentPayDto,
  ) {
    return ok(
      'Checkout started',
      await this.portal.startPay(tenantId, user.user_id, body),
    );
  }

  @Get('wards/:studentId/installments')
  async installments(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
  ) {
    return ok(
      'Ward installments',
      await this.portal.wardInstallments(tenantId, user.user_id, studentId),
    );
  }

  @Post('wards/:studentId/payment-proof')
  async submitPaymentProof(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
    @Body() body: ParentPaymentProofDto,
  ) {
    return ok(
      'Payment proof submitted',
      await this.portal.submitPaymentProof(
        tenantId,
        user.user_id,
        studentId,
        body,
      ),
    );
  }
}
