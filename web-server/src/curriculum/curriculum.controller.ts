import { Controller, Get, Param, Post, Body, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { CurriculumService } from './curriculum.service';

@Controller('curriculum')
export class CurriculumController {
  constructor(private readonly curriculum: CurriculumService) {}

  @Get('nerdc')
  @RequireFeature('academic.nerdc_curriculum')
  @RequirePermissions('classes:read', 'curriculum:view')
  async nerdc(@TenantId() tenantId: string) {
    return ok('NERDC packs', await this.curriculum.listNerdcPacks(tenantId));
  }

  @Get('nerdc/:packId')
  @RequireFeature('academic.nerdc_curriculum')
  @RequirePermissions('classes:read', 'curriculum:view')
  async nerdcPack(@TenantId() tenantId: string, @Param('packId') packId: string) {
    return ok('NERDC pack', await this.curriculum.getNerdcPack(tenantId, packId));
  }

  @Get('waec-neco/analytics')
  @RequireFeature('academic.waec_neco_analytics')
  @RequirePermissions('analytics:view', 'results:read')
  async waecAnalytics(@TenantId() tenantId: string) {
    return ok('WAEC/NECO analytics', await this.curriculum.waecNecoAnalytics(tenantId));
  }

  @Post('waec-neco/results')
  @RequireFeature('academic.waec_neco_analytics')
  @RequirePermissions('results:create', 'classes:manage')
  async seedWaec(
    @TenantId() tenantId: string,
    @Body() body: { studentId: string; examType: 'WAEC' | 'NECO'; subject: string; score: number; sessionLabel?: string },
  ) {
    return ok('Result stored', await this.curriculum.seedWaecResult(tenantId, body));
  }

  @Get('jamb/questions')
  @RequireFeature('academic.jamb_cbt')
  @RequirePermissions('classes:read', 'results:read')
  async jambQuestions(@TenantId() tenantId: string, @Query('subject') subject?: string) {
    return ok('JAMB questions', await this.curriculum.listJambQuestions(tenantId, subject));
  }

  @Post('jamb/questions')
  @RequireFeature('academic.jamb_cbt')
  @RequirePermissions('classes:manage', 'results:create')
  async addJambQuestion(
    @TenantId() tenantId: string,
    @Body() body: { subject: string; prompt: string; options: string[]; correctIndex: number; year?: number },
  ) {
    return ok('Question added', await this.curriculum.addJambQuestion(tenantId, body));
  }
}
