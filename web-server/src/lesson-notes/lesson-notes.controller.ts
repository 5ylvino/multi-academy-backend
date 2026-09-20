import { Body, Controller, Delete, Get, Param, Patch, Post, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { LessonNotesService } from './lesson-notes.service';

@Controller('lesson-notes')
@RequireFeature('academic.lesson_notes')
export class LessonNotesController {
  constructor(private readonly notes: LessonNotesService) {}

  @Get()
  @RequirePermissions('lesson_notes:read')
  async list(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('classId') classId?: string,
    @Query('subjectId') subjectId?: string,
    @Query('termId') termId?: string,
  ) {
    return ok('Lesson notes', await this.notes.list(tenantId, user.user_id || user.sub, { classId, subjectId, termId }));
  }

  @Post()
  @RequirePermissions('lesson_notes:manage')
  async create(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body()
    body: {
      classId: string;
      subjectId?: string;
      termId?: string;
      title: string;
      content: string;
      weekLabel?: string;
    },
  ) {
    return ok(
      'Lesson note created',
      await this.notes.create(tenantId, user.user_id || user.sub, body),
    );
  }

  @Patch(':id')
  @RequirePermissions('lesson_notes:manage')
  async update(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: Record<string, unknown>,
  ) {
    return ok('Lesson note updated', await this.notes.update(tenantId, user.user_id || user.sub, id, body as any));
  }

  @Delete(':id')
  @RequirePermissions('lesson_notes:manage')
  async remove(@TenantId() tenantId: string, @CurrentUser() user: AuthUserClaims, @Param('id') id: string) {
    return ok('Lesson note deleted', await this.notes.remove(tenantId, user.user_id || user.sub, id));
  }
}
