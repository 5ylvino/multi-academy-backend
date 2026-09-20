import {
  Body,
  Controller,
  Delete,
  Get,
  Header,
  Param,
  Patch,
  Post,
  Query,
  Res,
  StreamableFile,
} from '@nestjs/common';
import { Response } from 'express';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { RequirePermissions, RequireRoles } from '../common/auth/require-permissions.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { AcademicService, hasUnscopedClassAccess } from './academic.service';
import { GradingMatrixService } from './grading-matrix.service';
import { ReportCardsService } from './report-cards.service';
import { TermRankingService } from './term-ranking.service';
import { ListCacheService } from '../common/cache/list-cache.service';
import {
  ApproveResultsBatchDto,
  CreateSessionDto,
  CreateTermDto,
  EnrollStudentsDto,
  CreateSchemeOfWorkDto,
  SchemeTopicDto,
  UpdateSchemeOfWorkDto,
  UpdateSchemeTopicDto,
  UpdateSchemeTopicProgressDto,
  LinkParentStudentsDto,
  LinkTeacherClassesDto,
  LinkTeacherScopeDto,
  LinkTeacherSubjectsDto,
  UpdateAssignmentDto,
  UpdateClassDto,
  UpdateResultDto,
  UpdateSessionDto,
  UpdateSubjectDto,
  UpdateTermDto,
  UpsertAssignmentDto,
  UpsertAssignmentSubmissionDto,
  UpsertClassDto,
  UpsertResultDto,
  UpsertSubjectDto,
  SubjectCategoryDto,
  ScoreSheetBatchDto,
  ScoreSheetCategoryDto,
} from './dto/academic.dto';
import { RequireFeature } from '../platform-config/require-feature.decorator';

@Controller('academic')
@RequireFeature(
  'academic.sessions_terms',
  'academic.classes',
  'academic.subjects',
  'academic.enrollments',
  'academic.assignments',
  'academic.results',
)
export class AcademicController {
  constructor(
    private readonly academicService: AcademicService,
    private readonly gradingMatrix: GradingMatrixService,
    private readonly reportCards: ReportCardsService,
    private readonly termRanking: TermRankingService,
    private readonly listCache: ListCacheService,
  ) {  }

  @Post('cache/refresh')
  async refreshSchoolCaches(@TenantId() tenantId: string) {
    this.listCache.invalidateTenant(tenantId);
    return ok('School caches refreshed', { refreshed: true });
  }

  @Get('me/scope')
  @RequirePermissions('classes:read', 'subjects:read', 'results:read')
  async myScope(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Staff academic scope',
      await this.academicService.actorScope(tenantId, user.user_id || user.sub),
    );
  }

  @Get('results/pending-approval')
  @RequirePermissions('results:approve')
  async pendingApproval(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Pending result approvals',
      await this.academicService.pendingApproval(tenantId, user.user_id || user.sub),
    );
  }

  @Get('workflow')
  @RequirePermissions('classes:read', 'results:read')
  async workflow(@Query('schoolLevel') schoolLevel?: string) {
    const level = (schoolLevel || 'secondary').toLowerCase();
    if (level === 'nursery') {
      return ok('Academic workflow', {
        schoolLevel: 'nursery',
        primaryRecord: 'developmental',
        assessmentModel: 'developmental_domains',
        resultApprovalBy: 'head_teacher',
        supportsTranscript: false,
      });
    }
    if (level === 'primary') {
      return ok('Academic workflow', {
        schoolLevel: 'primary',
        primaryRecord: 'class',
        assessmentModel: 'class_teacher_subjects',
        resultApprovalBy: 'head_teacher',
        supportsTranscript: true,
      });
    }
    return ok('Academic workflow', {
      schoolLevel: 'secondary',
      primaryRecord: 'subject',
      assessmentModel: 'continuous_assessment_test_exam',
      resultApprovalBy: 'principal',
      supportsTranscript: true,
    });
  }

  @Get('calendar')
  @RequirePermissions(
    'academic_sessions:manage',
    'academic_terms:manage',
    'organization:view',
    'classes:read',
  )
  async getCalendar(@TenantId() tenantId: string) {
    return ok(
      'Academic calendar',
      await this.academicService.getCalendar(tenantId),
    );
  }

  @Get('structure-status')
  @RequirePermissions('classes:read', 'subjects:read', 'organization:view')
  async structureStatus(@TenantId() tenantId: string) {
    return ok(
      'Academic structure status',
      await this.academicService.getDefaultStructureStatus(tenantId),
    );
  }

  @Post('structure-repair')
  @RequirePermissions('classes:manage', 'subjects:manage')
  async repairStructure(@TenantId() tenantId: string) {
    return ok(
      'Academic structure repaired',
      await this.academicService.repairDefaultStructure(tenantId),
    );
  }

  @Get('sessions')
  @RequirePermissions('academic_sessions:manage', 'classes:read')
  async listSessions(
    @TenantId() tenantId: string,
    @Query('refresh') refresh?: string,
  ) {
    return ok('Sessions', await this.academicService.listSessions(tenantId, refresh === 'true'));
  }

  @Post('sessions')
  @RequirePermissions('academic_sessions:manage')
  async createSession(
    @TenantId() tenantId: string,
    @Body() body: CreateSessionDto,
  ) {
    return ok(
      'Session created',
      await this.academicService.createSession(tenantId, body),
    );
  }

  @Patch('sessions/:id')
  @RequirePermissions('academic_sessions:manage')
  async updateSession(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: UpdateSessionDto,
  ) {
    return ok(
      'Session updated',
      await this.academicService.updateSession(tenantId, id, body),
    );
  }

  @Delete('sessions/:id')
  @RequirePermissions('academic_sessions:manage')
  async deleteSession(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok(
      'Session deleted',
      await this.academicService.deleteSession(tenantId, id),
    );
  }

  @Post('sessions/:id/set-current')
  @RequirePermissions('academic_sessions:manage')
  async setCurrentSession(
    @TenantId() tenantId: string,
    @Param('id') id: string,
  ) {
    return ok(
      'Current session set',
      await this.academicService.setCurrentSession(tenantId, id),
    );
  }

  @Get('terms')
  // Terms are also needed by teachers for read-only academic filters.
  @RequirePermissions('academic_terms:manage', 'classes:read')
  async listTerms(
    @TenantId() tenantId: string,
    @Query('sessionId') sessionId?: string,
    @Query('refresh') refresh?: string,
  ) {
    return ok(
      'Terms',
      await this.academicService.listTerms(tenantId, sessionId, refresh === 'true'),
    );
  }

  @Post('terms')
  @RequirePermissions('academic_terms:manage')
  async createTerm(@TenantId() tenantId: string, @Body() body: CreateTermDto) {
    return ok(
      'Term created',
      await this.academicService.createTerm(tenantId, body),
    );
  }

  @Patch('terms/:id')
  @RequirePermissions('academic_terms:manage')
  async updateTerm(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: UpdateTermDto,
  ) {
    return ok(
      'Term updated',
      await this.academicService.updateTerm(tenantId, id, body),
    );
  }

  @Delete('terms/:id')
  @RequirePermissions('academic_terms:manage')
  async deleteTerm(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok(
      'Term deleted',
      await this.academicService.deleteTerm(tenantId, id),
    );
  }

  @Post('terms/:id/set-current')
  @RequirePermissions('academic_terms:manage')
  async setCurrentTerm(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok(
      'Current term set',
      await this.academicService.setCurrentTerm(tenantId, id),
    );
  }

  @Get('classes')
  @RequirePermissions('classes:read', 'classes:manage')
  async listClasses(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('refresh') refresh?: string,
  ) {
    const scopeToTeacher = !hasUnscopedClassAccess(
      user.permissions,
      user.capabilities,
    );
    return ok(
      'Classes',
      await this.academicService.listClasses(
        tenantId,
        user.user_id || user.sub,
        scopeToTeacher,
        refresh === 'true',
      ),
    );
  }

  @Post('classes')
  @RequirePermissions('classes:create', 'classes:manage')
  async createClass(
    @TenantId() tenantId: string,
    @Body() body: UpsertClassDto,
  ) {
    return ok(
      'Class saved',
      await this.academicService.upsertClass(tenantId, null, body),
    );
  }

  @Patch('classes/:id')
  @RequirePermissions('classes:update', 'classes:manage')
  async updateClass(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: UpdateClassDto,
  ) {
    return ok(
      'Class updated',
      await this.academicService.upsertClass(tenantId, id, body),
    );
  }

  @Delete('classes/:id')
  @RequirePermissions('classes:delete', 'classes:manage')
  async deleteClass(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok(
      'Class deleted',
      await this.academicService.deleteClass(tenantId, id),
    );
  }

  @Get('subjects')
  @RequirePermissions('subjects:read', 'subjects:manage')
  async listSubjects(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('refresh') refresh?: string,
  ) {
    const scopeToTeacher = !hasUnscopedClassAccess(
      user.permissions,
      user.capabilities,
    );
    return ok(
      'Subjects',
      await this.academicService.listSubjects(
        tenantId,
        user.user_id || user.sub,
        scopeToTeacher,
        refresh === 'true',
      ),
    );
  }

  @Get('subject-categories')
  @RequirePermissions('subjects:read', 'subjects:manage')
  async listSubjectCategories(@TenantId() tenantId: string) {
    return ok('Subject categories', await this.academicService.listSubjectCategories(tenantId));
  }

  @Post('subject-categories')
  @RequirePermissions('subjects:create', 'subjects:manage')
  async createSubjectCategory(
    @TenantId() tenantId: string,
    @Body() body: SubjectCategoryDto,
  ) {
    return ok('Subject category created', await this.academicService.createSubjectCategory(tenantId, body));
  }

  @Get('score-sheets/categories')
  @RequirePermissions('results:read', 'results:manage')
  async listScoreSheetCategories(@TenantId() tenantId: string) {
    return ok(
      'Score sheet categories',
      await this.academicService.listScoreSheetCategories(tenantId),
    );
  }

  @Post('score-sheets/categories')
  @RequirePermissions('results:create', 'results:manage')
  async createScoreSheetCategory(
    @TenantId() tenantId: string,
    @Body() body: ScoreSheetCategoryDto,
  ) {
    return ok(
      'Score sheet category created',
      await this.academicService.createScoreSheetCategory(tenantId, body),
    );
  }

  @Patch('score-sheets/categories/:id')
  @RequirePermissions('results:update', 'results:manage')
  async updateScoreSheetCategory(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: ScoreSheetCategoryDto,
  ) {
    return ok(
      'Score sheet category updated',
      await this.academicService.updateScoreSheetCategory(tenantId, id, body),
    );
  }

  @Delete('score-sheets/categories/:id')
  @RequirePermissions('results:delete', 'results:manage')
  async deleteScoreSheetCategory(
    @TenantId() tenantId: string,
    @Param('id') id: string,
  ) {
    return ok(
      'Score sheet category deleted',
      await this.academicService.deleteScoreSheetCategory(tenantId, id),
    );
  }

  @Get('score-sheets')
  @RequireRoles('subject_teacher', 'head_teacher', 'principal', 'vice_principal', 'assistant_head_teacher')
  @RequirePermissions('results:read', 'results:manage')
  async listScoreSheets(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('sessionId') sessionId?: string,
    @Query('termId') termId?: string,
    @Query('classId') classId?: string,
    @Query('refresh') refresh?: string,
    @Query('subjectId') subjectId?: string,
    @Query('categoryId') categoryId?: string,
    @Query('subcategoryId') subcategoryId?: string,
  ) {
    return ok(
      'Score sheets',
      await this.academicService.listScoreSheets(
        tenantId,
        user.user_id || user.sub,
        { sessionId, termId, classId, subjectId, categoryId, subcategoryId },
      ),
    );
  }

  @Get('score-sheets/preview')
  @RequireRoles('subject_teacher', 'head_teacher', 'principal', 'assistant_head_teacher')
  @RequirePermissions('results:read', 'results:manage')
  async previewScoreSheets(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('sessionId') sessionId: string,
    @Query('termId') termId: string,
    @Query('classId') classId: string,
    @Query('studentName') studentName?: string,
  ) {
    return ok(
      'Score sheet preview',
      await this.academicService.previewScoreSheets(
        tenantId,
        user.user_id || user.sub,
        sessionId,
        termId,
        classId,
        studentName,
      ),
    );
  }

  @Post('score-sheets/:studentId/generate-result')
  @RequireRoles('subject_teacher')
  @RequirePermissions('results:update', 'results:manage')
  async generateScoreSheetResult(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
    @Query('sessionId') sessionId: string,
    @Query('termId') termId: string,
    @Query('classId') classId: string,
  ) {
    return ok(
      'Result generated',
      await this.academicService.generateScoreSheetResult(
        tenantId,
        user.user_id || user.sub,
        studentId,
        sessionId,
        termId,
        classId,
      ),
    );
  }

  @Post('score-sheets')
  @RequireRoles('subject_teacher')
  @RequirePermissions('results:create', 'results:manage')
  async saveScoreSheets(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: ScoreSheetBatchDto,
  ) {
    return ok(
      'Score sheets saved',
      await this.academicService.saveScoreSheetBatch(
        tenantId,
        user.user_id || user.sub,
        body,
      ),
    );
  }

  @Post('score-sheets/publish')
  @RequireRoles('subject_teacher')
  @RequirePermissions('results:update', 'results:manage')
  async publishScoreSheets(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: { ids: string[] },
  ) {
    return ok(
      'Score sheets published',
      await this.academicService.publishScoreSheets(
        tenantId,
        user.user_id || user.sub,
        body.ids || [],
      ),
    );
  }

  @Delete('score-sheets/:id')
  @RequireRoles('subject_teacher')
  @RequirePermissions('results:update', 'results:manage')
  async deleteScoreSheet(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok(
      'Score sheet deleted',
      await this.academicService.deleteScoreSheet(
        tenantId,
        user.user_id || user.sub,
        id,
      ),
    );
  }

  @Post('subjects')
  @RequirePermissions('subjects:create', 'subjects:manage')
  async createSubject(
    @TenantId() tenantId: string,
    @Body() body: UpsertSubjectDto,
  ) {
    return ok(
      'Subject saved',
      await this.academicService.upsertSubject(tenantId, null, body),
    );
  }

  @Patch('subjects/:id')
  @RequirePermissions('subjects:update', 'subjects:manage')
  async updateSubject(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: UpdateSubjectDto,
  ) {
    return ok(
      'Subject updated',
      await this.academicService.upsertSubject(tenantId, id, body),
    );
  }

  @Delete('subjects/:id')
  @RequirePermissions('subjects:delete', 'subjects:manage')
  async deleteSubject(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok(
      'Subject deleted',
      await this.academicService.deleteSubject(tenantId, id),
    );
  }

  @Get('assignments')
  @RequirePermissions('assignments:read', 'assignments:manage')
  async listAssignments(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Assignments',
      await this.academicService.listAssignments(
        tenantId,
        user.user_id || user.sub,
      ),
    );
  }

  @Post('assignments')
  @RequirePermissions('assignments:create', 'assignments:manage')
  async createAssignment(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: UpsertAssignmentDto,
  ) {
    return ok(
      'Assignment saved',
      await this.academicService.upsertAssignment(tenantId, null, {
        ...body,
        assignedBy: user.user_id,
      }),
    );
  }

  @Patch('assignments/:id')
  @RequirePermissions('assignments:update', 'assignments:manage')
  async updateAssignment(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: UpdateAssignmentDto,
  ) {
    return ok(
      'Assignment updated',
      await this.academicService.upsertAssignment(tenantId, id, {
        ...body,
        assignedBy: user.user_id || user.sub,
      }),
    );
  }

  @Delete('assignments/:id')
  @RequirePermissions('assignments:delete', 'assignments:manage')
  async deleteAssignment(
    @TenantId() tenantId: string,
    @Param('id') id: string,
  ) {
    return ok(
      'Assignment deleted',
      await this.academicService.deleteAssignment(tenantId, id),
    );
  }

  @Get('assignments/:id/submissions')
  @RequirePermissions('assignments:read', 'assignments:manage')
  async listAssignmentSubmissions(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Assignment submissions',
      await this.academicService.listAssignmentSubmissions(
        tenantId,
        id,
        user.user_id || user.sub,
      ),
    );
  }

  @Post('assignments/:id/submissions')
  @RequirePermissions(
    'assignments:update',
    'assignments:manage',
    'assignments:create',
  )
  async upsertAssignmentSubmission(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: UpsertAssignmentSubmissionDto,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Submission saved',
      await this.academicService.upsertAssignmentSubmission(
        tenantId,
        id,
        body,
        user.user_id || user.sub,
      ),
    );
  }

  @Get('schemes-of-work')
  @RequireFeature('academic.scheme_of_work')
  @RequirePermissions('curriculum:view', 'classes:read', 'subjects:read', 'results:read')
  async listSchemes(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('classId') classId?: string,
    @Query('subjectId') subjectId?: string,
    @Query('termId') termId?: string,
  ) {
    return ok(
      'Schemes of work',
      await this.academicService.listSchemes(tenantId, user.user_id || user.sub, {
        classId,
        subjectId,
        termId,
      }),
    );
  }

  @Get('schemes-of-work/:id')
  @RequireFeature('academic.scheme_of_work')
  @RequirePermissions('curriculum:view', 'classes:read', 'subjects:read', 'results:read')
  async getScheme(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok('Scheme of work', await this.academicService.getScheme(tenantId, user.user_id || user.sub, id));
  }

  @Post('schemes-of-work')
  @RequireFeature('academic.scheme_of_work')
  @RequireRoles('head_teacher', 'principal', 'vice_principal')
  async createScheme(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: CreateSchemeOfWorkDto,
  ) {
    return ok(
      'Scheme of work created',
      await this.academicService.createScheme(tenantId, user.user_id || user.sub, body),
    );
  }

  @Patch('schemes-of-work/:id')
  @RequireFeature('academic.scheme_of_work')
  @RequireRoles('head_teacher', 'principal', 'vice_principal')
  async updateScheme(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: UpdateSchemeOfWorkDto,
  ) {
    return ok(
      'Scheme of work updated',
      await this.academicService.updateScheme(tenantId, user.user_id || user.sub, id, body),
    );
  }

  @Delete('schemes-of-work/:id')
  @RequireFeature('academic.scheme_of_work')
  @RequireRoles('head_teacher', 'principal', 'vice_principal')
  async deleteScheme(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok(
      'Scheme of work deleted',
      await this.academicService.deleteScheme(tenantId, user.user_id || user.sub, id),
    );
  }

  @Post('schemes-of-work/:id/topics')
  @RequireFeature('academic.scheme_of_work')
  @RequireRoles('head_teacher', 'principal', 'vice_principal')
  async createSchemeTopic(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: SchemeTopicDto,
  ) {
    return ok(
      'Scheme topic created',
      await this.academicService.createSchemeTopic(tenantId, user.user_id || user.sub, id, body),
    );
  }

  @Patch('scheme-topics/:topicId')
  @RequireFeature('academic.scheme_of_work')
  @RequireRoles('head_teacher', 'principal', 'vice_principal')
  async updateSchemeTopic(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('topicId') topicId: string,
    @Body() body: UpdateSchemeTopicDto,
  ) {
    return ok(
      'Scheme topic updated',
      await this.academicService.updateSchemeTopic(tenantId, user.user_id || user.sub, topicId, body),
    );
  }

  @Delete('scheme-topics/:topicId')
  @RequireFeature('academic.scheme_of_work')
  @RequireRoles('head_teacher', 'principal', 'vice_principal')
  async deleteSchemeTopic(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('topicId') topicId: string,
  ) {
    return ok(
      'Scheme topic deleted',
      await this.academicService.deleteSchemeTopic(tenantId, user.user_id || user.sub, topicId),
    );
  }

  @Patch('scheme-topics/:topicId/progress')
  @RequireFeature('academic.scheme_of_work')
  @RequireRoles('class_teacher', 'subject_teacher', 'head_teacher', 'principal')
  async updateSchemeTopicProgress(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('topicId') topicId: string,
    @Body() body: UpdateSchemeTopicProgressDto,
  ) {
    return ok(
      'Scheme topic progress updated',
      await this.academicService.updateSchemeTopicProgress(
        tenantId,
        user.user_id || user.sub,
        topicId,
        body,
      ),
    );
  }

  @Get('results')
  @RequirePermissions('results:read', 'results:manage')
  async listResults(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('refresh') refresh?: string,
  ) {
    return ok(
      'Results',
      await this.academicService.listResults(
        tenantId,
        user.user_id || user.sub,
        refresh === 'true',
      ),
    );
  }

  @Get('results/summary')
  @RequirePermissions('results:read', 'results:manage')
  async listResultsSummary(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Results Summary',
      await this.academicService.listResultsSummary(
        tenantId,
        user.user_id || user.sub,
      ),
    );
  }

  @Get('generated-results')
  async listGeneratedResults(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('sessionId') sessionId?: string,
    @Query('termId') termId?: string,
    @Query('classId') classId?: string,
    @Query('refresh') refresh?: string,
  ) {
    return ok(
      'Generated results',
      await this.academicService.listGeneratedResults(
        tenantId,
        user.user_id || user.sub,
        { sessionId, termId, classId },
        refresh === 'true',
      ),
    );
  }

  @Get('generated-results/:id')
  async getGeneratedResult(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok(
      'Generated result',
      await this.academicService.getGeneratedResult(
        tenantId,
        user.user_id || user.sub,
        id,
      ),
    );
  }

  @Post('generated-results/:id/release')
  @RequireRoles('director', 'school_admin', 'head_teacher', 'principal', 'vice_principal', 'assistant_head_teacher')
  @RequirePermissions('results:approve')
  async releaseGeneratedResult(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
  ) {
    return ok(
      'Generated result released',
      await this.academicService.releaseGeneratedResult(
        tenantId,
        user.user_id || user.sub,
        id,
      ),
    );
  }

  @Post('results')
  @RequirePermissions('results:create', 'results:manage')
  async createResult(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: UpsertResultDto,
  ) {
    return ok(
      'Result saved',
      await this.academicService.upsertResult(tenantId, null, {
        ...body,
        actorUserId: user.user_id || user.sub,
      }),
    );
  }

  @Post('results/approve-batch')
  @RequirePermissions('results:approve')
  async approveResultsBatch(
    @TenantId() tenantId: string,
    @Body() body: ApproveResultsBatchDto,
  ) {
    return ok(
      'Results approved',
      await this.academicService.approveResultsBatch(tenantId, body),
    );
  }

  @Post('results/:id/approve')
  @RequirePermissions('results:approve')
  async approveResult(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: { comment?: string },
  ) {
    return ok(
      'Result approved',
      await this.academicService.approveResult(
        tenantId,
        id,
        user.user_id || user.sub,
        body?.comment,
      ),
    );
  }

  @Patch('results/:id')
  @RequirePermissions('results:update', 'results:manage')
  async updateResult(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('id') id: string,
    @Body() body: UpdateResultDto,
  ) {
    return ok(
      'Result updated',
      await this.academicService.upsertResult(tenantId, id, {
        ...body,
        actorUserId: user.user_id || user.sub,
      }),
    );
  }

  @Delete('results/:id')
  @RequirePermissions('results:delete', 'results:manage')
  async deleteResult(@TenantId() tenantId: string, @Param('id') id: string) {
    return ok(
      'Result deleted',
      await this.academicService.deleteResult(tenantId, id),
    );
  }

  @Get('classes/:id/enrollments')
  @RequirePermissions('classes:read', 'classes:manage', 'students:read')
  async getClassEnrollments(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Class enrollments',
      await this.academicService.getClassEnrollments(tenantId, id, user.user_id || user.sub),
    );
  }

  @Post('classes/:id/enrollments')
  @RequirePermissions('classes:update', 'classes:manage', 'students:update')
  async enrollStudents(
    @TenantId() tenantId: string,
    @Param('id') id: string,
    @Body() body: EnrollStudentsDto,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'Students enrolled',
      await this.academicService.enrollStudentsInClass(
        tenantId,
        id,
        body.studentIds || [],
        user.user_id || user.sub,
      ),
    );
  }

  @Get('links/teacher/:teacherId')
  @RequirePermissions('classes:read', 'subjects:read', 'roles:assign')
  async getTeacherLinks(
    @TenantId() tenantId: string,
    @Param('teacherId') teacherId: string,
  ) {
    return ok(
      'Teacher links',
      await this.academicService.getTeacherLinks(tenantId, teacherId),
    );
  }

  @Get('links/parent/:parentId')
  @RequirePermissions('students:read', 'students:update', 'roles:assign')
  async getParentLinks(
    @TenantId() tenantId: string,
    @Param('parentId') parentId: string,
  ) {
    return ok(
      'Parent links',
      await this.academicService.getParentLinks(tenantId, parentId),
    );
  }

  @Post('links/teacher-classes')
  @RequirePermissions('classes:manage', 'roles:assign')
  async linkTeacherClasses(
    @TenantId() tenantId: string,
    @Body() body: LinkTeacherClassesDto,
  ) {
    return ok(
      'Teacher classes linked',
      await this.academicService.linkTeacherToClasses(
        tenantId,
        body.teacherId,
        body.classIds || [],
      ),
    );
  }

  @Post('links/teacher-subjects')
  @RequirePermissions('subjects:manage', 'roles:assign')
  async linkTeacherSubjects(
    @TenantId() tenantId: string,
    @Body() body: LinkTeacherSubjectsDto,
  ) {
    return ok(
      'Teacher subjects linked',
      await this.academicService.linkTeacherToSubjects(
        tenantId,
        body.teacherId,
        body.subjectIds || [],
      ),
    );
  }

  @Post('links/teacher-scope')
  @RequirePermissions('roles:assign', 'classes:manage', 'subjects:manage')
  async linkTeacherScope(
    @TenantId() tenantId: string,
    @Body() body: LinkTeacherScopeDto,
  ) {
    return ok(
      'Teacher scope linked',
      await this.academicService.linkTeacherScope(
        tenantId,
        body.teacherId,
        body.classIds || [],
        body.subjectIds || [],
        body.schoolLevels || [],
      ),
    );
  }

  @Post('links/parent-students')
  @RequirePermissions('students:update', 'roles:assign')
  async linkParentStudents(
    @TenantId() tenantId: string,
    @Body() body: LinkParentStudentsDto,
  ) {
    return ok(
      'Parent students linked',
      await this.academicService.linkParentToStudents(
        tenantId,
        body.parentId,
        body.studentIds || [],
      ),
    );
  }

  @Get('grading-matrix')
  @RequireFeature('academic.grading_matrix')
  @RequirePermissions(
    'results:manage',
    'results:read',
    'academic_sessions:manage',
  )
  async getGradingMatrix(@TenantId() tenantId: string) {
    return ok('Grading matrix', await this.gradingMatrix.get(tenantId));
  }

  @Post('grading-matrix')
  @RequireFeature('academic.grading_matrix')
  @RequirePermissions('results:manage', 'academic_sessions:manage')
  async setGradingMatrix(
    @TenantId() tenantId: string,
    @Body() body: { caWeight: number; examWeight: number; gradeBands?: any[] },
  ) {
    return ok(
      'Grading matrix saved',
      await this.gradingMatrix.set(tenantId, body),
    );
  }

  @Get('ranking')
  @RequireFeature('academic.term_ranking')
  @RequirePermissions('results:read', 'results:manage', 'classes:read')
  async ranking(
    @TenantId() tenantId: string,
    @Query('classId') classId: string,
    @Query('termId') termId: string,
  ) {
    return ok(
      'Term ranking',
      await this.termRanking.rankClass(tenantId, classId, termId),
    );
  }

  @Get('report-cards/:studentId')
  @RequireFeature('academic.report_cards')
  @RequirePermissions('results:read', 'results:manage', 'students:read')
  async reportCardData(
    @TenantId() tenantId: string,
    @Param('studentId') studentId: string,
    @Query('termId') termId: string,
  ) {
    return ok(
      'Report card',
      await this.reportCards.getData(tenantId, studentId, termId),
    );
  }

  @Get('report-cards/:studentId/pdf')
  @RequireFeature('academic.report_cards')
  @RequirePermissions('results:read', 'results:manage', 'students:read')
  @Header('Content-Type', 'application/pdf')
  async reportCardPdf(
    @TenantId() tenantId: string,
    @Param('studentId') studentId: string,
    @Query('termId') termId: string,
    @Res({ passthrough: true }) res: Response,
  ) {
    const buf = await this.reportCards.pdf(tenantId, studentId, termId);
    res.setHeader(
      'Content-Disposition',
      `attachment; filename="report-card-${studentId}-${termId}.pdf"`,
    );
    return new StreamableFile(Uint8Array.from(buf));
  }

  @Get('transcripts/:studentId')
  @RequirePermissions('results:read', 'results:manage', 'students:read')
  async transcript(
    @TenantId() tenantId: string,
    @Param('studentId') studentId: string,
  ) {
    return ok(
      'Transcript',
      await this.reportCards.transcriptData(tenantId, studentId),
    );
  }

  @Get('students/:studentId/history')
  @RequirePermissions('results:read', 'students:read')
  async studentHistory(@TenantId() tenantId: string, @Param('studentId') studentId: string) {
    return ok('Student academic history', await this.academicService.getStudentHistory(tenantId, studentId));
  }

  @Post('students/:studentId/promote')
  @RequirePermissions('students:update', 'classes:manage')
  async promoteStudent(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Param('studentId') studentId: string,
    @Body() body: { toClassId: string; sessionId?: string; notes?: string },
  ) {
    return ok('Student promoted', await this.academicService.promoteStudent(tenantId, {
      ...body, studentId, promotedBy: user.user_id || user.sub,
    }));
  }

  @Get('transcripts/:studentId/pdf')
  @RequireRoles(
    'director',
    'school_admin',
    'head_teacher',
    'principal',
    'vice_principal',
    'assistant_head_teacher',
    'class_teacher',
    'subject_teacher',
    'administrative_staff',
    'bursar',
    'student',
    'parent',
  )
  @RequirePermissions('results:read', 'results:manage', 'students:read')
  @Header('Content-Type', 'application/pdf')
  async transcriptPdf(
    @TenantId() tenantId: string,
    @Param('studentId') studentId: string,
    @Res({ passthrough: true }) res: Response,
  ) {
    const buf = await this.reportCards.transcriptPdf(tenantId, studentId);
    res.setHeader(
      'Content-Disposition',
      `attachment; filename="transcript-${studentId}.pdf"`,
    );
    return new StreamableFile(Uint8Array.from(buf));
  }
}
