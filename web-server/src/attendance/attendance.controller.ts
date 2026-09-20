import { Body, Controller, Get, Post, Query } from '@nestjs/common';
import { ok } from '../common/types/api-response';
import { TenantId } from '../common/auth/tenant-id.decorator';
import { CurrentUser } from '../common/auth/current-user.decorator';
import { RequirePermissions } from '../common/auth/require-permissions.decorator';
import { AuthUserClaims } from '../common/auth/auth-user.interface';
import { AttendanceService } from './attendance.service';
import { AbsenceAlertsService } from './absence-alerts.service';
import {
  MarkStaffAttendanceDto,
  MarkStaffManualAttendanceDto,
  MarkStudentAttendanceDto,
} from './dto/attendance.dto';
import { RequireFeature } from '../platform-config/require-feature.decorator';
import { schoolIsoDate } from '../common/utils/school-session.util';

@Controller('attendance')
@RequireFeature('attendance.students', 'attendance.staff')
export class AttendanceController {
  constructor(
    private readonly attendanceService: AttendanceService,
    private readonly absenceAlerts: AbsenceAlertsService,
  ) {}

  @Get('students')
  @RequirePermissions('student_attendance:read', 'student_attendance:view_all')
  async students(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Query('date') date?: string,
    @Query('classId') classId?: string,
  ) {
    const data = await this.attendanceService.listStudentAttendance(
      tenantId,
      date || schoolIsoDate(),
      classId,
      user.user_id || user.sub,
    );
    return ok('Student attendance', data);
  }

  @Post('students/mark')
  @RequirePermissions('student_attendance:create', 'student_attendance:update')
  async markStudent(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: MarkStudentAttendanceDto,
  ) {
    const data = await this.attendanceService.markStudentAttendance({
      tenantId,
      userId: user.user_id,
      studentId: body.studentId,
      classId: body.classId,
      date: body.date,
      status: body.status,
    });
    return ok('Student attendance marked', data);
  }

  @Post('students/correct')
  @RequirePermissions(
    'student_attendance:update',
    'student_attendance:view_all',
  )
  async correctStudent(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body()
    body: MarkStudentAttendanceDto & { reason: string },
  ) {
    const data = await this.attendanceService.correctStudentAttendance({
      tenantId,
      actorUserId: user.user_id,
      studentId: body.studentId,
      classId: body.classId,
      date: body.date,
      status: body.status,
      reason: body.reason,
    });
    return ok('Student attendance corrected', data);
  }

  @Get('staff')
  @RequirePermissions('staff_attendance:view_all')
  async staff(
    @TenantId() tenantId: string,
    @Query('date') date?: string,
    @Query('schoolLevel') schoolLevel?: string,
  ) {
    const data = await this.attendanceService.listStaffAttendance(
      tenantId,
      date || schoolIsoDate(),
      schoolLevel,
    );
    return ok('Staff attendance', data);
  }

  @Get('staff/me')
  @RequirePermissions(
    'staff_attendance:read',
    'staff_attendance:create',
    'staff_attendance:view_all',
  )
  async myStaffAttendance(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    return ok(
      'My staff attendance',
      await this.attendanceService.listMyStaffAttendance(
        tenantId,
        user.user_id || user.sub,
      ),
    );
  }

  @Get('staff/verification-config')
  @RequirePermissions(
    'staff_attendance:create',
    'staff_attendance:read',
    'organization_settings:configure',
  )
  async staffVerificationConfig(@TenantId() tenantId: string) {
    const data = await this.attendanceService.getStaffVerificationConfig(
      tenantId,
    );
    return ok('Staff verification locations', data);
  }

  @Get('staff/daily-verification-status')
  async staffDailyVerificationStatus(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    const data = await this.attendanceService.getStaffDailyVerificationStatus(
      tenantId,
      user.user_id || user.sub,
      user.roles || [],
    );
    return ok('Staff daily verification status', data);
  }

  @Post('staff/mark')
  @RequirePermissions('staff_attendance:create')
  async markStaff(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: MarkStaffAttendanceDto,
  ) {
    const data = await this.attendanceService.markStaffAttendance({
      tenantId,
      actorUserId: user.user_id,
      staffId: body.staffId || user.user_id,
      date: body.date,
      status: body.status,
      biometricAssertionId: body.biometricAssertionId,
      location: body.location,
      latitude: body.latitude,
      longitude: body.longitude,
    });
    return ok('Staff attendance marked', data);
  }

  @Post('staff/mark-manual')
  @RequirePermissions('staff_attendance:create')
  async markStaffManual(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
    @Body() body: MarkStaffManualAttendanceDto,
  ) {
    const data = await this.attendanceService.markStaffAttendanceWithManualCode(
      {
        tenantId,
        actorUserId: user.user_id,
        staffId: body.staffId || user.user_id,
        date: body.date,
        status: body.status,
        verificationCode: body.verificationCode,
        location: body.location,
        latitude: body.latitude,
        longitude: body.longitude,
      },
    );
    return ok('Staff attendance marked with manual code', data);
  }

  @Get('staff/manual-code')
  @RequirePermissions('organization_settings:configure')
  async getManualCode(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    const data = await this.attendanceService.getManualVerificationCode(
      tenantId,
      user.user_id,
    );
    return ok('Manual verification code', data);
  }

  @Post('staff/manual-code/regenerate')
  @RequirePermissions('organization_settings:configure')
  async regenerateManualCode(
    @TenantId() tenantId: string,
    @CurrentUser() user: AuthUserClaims,
  ) {
    const data = await this.attendanceService.regenerateManualVerificationCode(
      tenantId,
      user.user_id,
    );
    return ok('Manual verification code regenerated', data);
  }

  @Get('absence-alerts/config')
  @RequireFeature('attendance.absence_alerts')
  @RequirePermissions(
    'student_attendance:view_all',
    'organization_settings:configure',
  )
  async absenceConfig(@TenantId() tenantId: string) {
    return ok(
      'Absence alert config',
      await this.absenceAlerts.getConfig(tenantId),
    );
  }

  @Post('absence-alerts/config')
  @RequireFeature('attendance.absence_alerts')
  @RequirePermissions('organization_settings:configure')
  async setAbsenceConfig(
    @TenantId() tenantId: string,
    @Body()
    body: {
      thresholdDays?: number;
      windowDays?: number;
      notifyParents?: boolean;
      notifyStaff?: boolean;
    },
  ) {
    return ok(
      'Absence alert config saved',
      await this.absenceAlerts.setConfig(tenantId, body),
    );
  }

  @Post('absence-alerts/run')
  @RequireFeature('attendance.absence_alerts')
  @RequirePermissions(
    'student_attendance:view_all',
    'organization_settings:configure',
  )
  async runAbsenceAlerts(@TenantId() tenantId: string) {
    return ok(
      'Absence alerts processed',
      await this.absenceAlerts.run(tenantId),
    );
  }
}
