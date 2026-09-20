import { Body, Controller, Get, Param, Post } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { CbtService } from './cbt.service';
import { IsArray, IsNumber, IsOptional, IsString, Max, Min, MinLength } from 'class-validator';

class CreateCbtDto {
  @IsString()
  @MinLength(1)
  title!: string;

  @IsOptional()
  @IsString()
  subjectId?: string;

  @IsOptional()
  @IsString()
  classId?: string;

  @IsOptional()
  @IsNumber()
  @Min(1)
  @Max(480)
  durationMinutes?: number;

  @IsOptional()
  @IsArray()
  questions?: { prompt: string; options: string[]; correctIndex: number; points?: number }[];
}

@Controller('cbt')
@RequireFeature('academic.cbt')
export class CbtController {
  constructor(private readonly cbt: CbtService) {}

  @Get('exams')
  @RequirePermissions('cbt:read', 'cbt:manage', 'cbt:take')
  async list(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'CBT exams',
      await this.cbt.listExams(tenantId, user.user_id || user.sub),
    );
  }

  @Post('exams')
  @RequirePermissions('cbt:manage')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateCbtDto,
  ) {
    return ok(
      'CBT exam created',
      await this.cbt.createExam(tenantId, user.user_id || user.sub, body),
    );
  }

  @Post('exams/:examId/publish')
  @RequirePermissions('cbt:publish')
  async publish(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('examId') examId: string,
  ) {
    return ok(
      'CBT exam published',
      await this.cbt.publishExam(tenantId, user.user_id || user.sub, examId),
    );
  }

  @Post('exams/:examId/attempts/start')
  @RequirePermissions('cbt:take', 'cbt:manage')
  async startAttempt(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('examId') examId: string,
  ) {
    return ok(
      'Attempt started',
      await this.cbt.startAttempt(tenantId, user.user_id || user.sub, examId),
    );
  }

  @Post('attempts/:attemptId/submit')
  @RequirePermissions('cbt:take', 'cbt:manage')
  async submitAnswers(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('attemptId') attemptId: string,
    @Body() body: { answers: Record<string, number> },
  ) {
    return ok(
      'Attempt scored',
      await this.cbt.submitAnswers(
        tenantId,
        user.user_id || user.sub,
        attemptId,
        body.answers || {},
      ),
    );
  }
}
