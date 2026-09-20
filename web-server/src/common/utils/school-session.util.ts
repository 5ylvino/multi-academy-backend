import type { AttendanceConfigDto } from '../../organizations/dto/organization.dto';

const DEFAULT_BUSINESS_DAYS = [1, 2, 3, 4, 5];
const DEFAULT_SESSION_START = '08:00';
const DEFAULT_SESSION_END = '16:00';
const SCHOOL_TIME_ZONE = 'Africa/Lagos';

function localParts(now: Date) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: SCHOOL_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(now);
  const value = (type: string) => Number(parts.find((part) => part.type === type)?.value || 0);
  return {
    year: value('year'),
    month: value('month'),
    day: value('day'),
    hour: value('hour'),
    minute: value('minute'),
    weekday: new Intl.DateTimeFormat('en-US', {
      timeZone: SCHOOL_TIME_ZONE,
      weekday: 'short',
    }).format(now),
  };
}

export function schoolIsoDate(now = new Date()): string {
  const parts = localParts(now);
  return `${parts.year}-${String(parts.month).padStart(2, '0')}-${String(parts.day).padStart(2, '0')}`;
}

function parseTimeToMinutes(value?: string | null): number | null {
  if (!value || typeof value !== 'string') return null;
  const match = value.trim().match(/^(\d{1,2}):(\d{2})(?::\d{2})?$/);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes) || hours > 23 || minutes > 59) {
    return null;
  }
  return hours * 60 + minutes;
}

export function resolveSessionConfig(config?: AttendanceConfigDto | null) {
  return {
    sessionStartTime: config?.sessionStartTime || DEFAULT_SESSION_START,
    sessionEndTime: config?.sessionEndTime || DEFAULT_SESSION_END,
    biometricWindowStart: config?.biometricWindowStart || config?.sessionStartTime || DEFAULT_SESSION_START,
    biometricWindowEnd: config?.biometricWindowEnd || config?.sessionEndTime || DEFAULT_SESSION_END,
    businessDays:
      Array.isArray(config?.businessDays) && config!.businessDays!.length > 0
        ? config!.businessDays!
        : DEFAULT_BUSINESS_DAYS,
  };
}

export function isBusinessDay(config: AttendanceConfigDto | null | undefined, date = new Date()): boolean {
  const { businessDays } = resolveSessionConfig(config);
  const weekday = localParts(date).weekday;
  const day = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(weekday);
  return businessDays.includes(day);
}

export function isWithinTimeWindow(
  config: AttendanceConfigDto | null | undefined,
  startKey: 'biometricWindowStart' | 'sessionStartTime',
  endKey: 'biometricWindowEnd' | 'sessionEndTime',
  now = new Date(),
): boolean {
  const resolved = resolveSessionConfig(config);
  const start = parseTimeToMinutes(resolved[startKey === 'sessionStartTime' ? 'sessionStartTime' : 'biometricWindowStart']);
  const end = parseTimeToMinutes(resolved[endKey === 'sessionEndTime' ? 'sessionEndTime' : 'biometricWindowEnd']);
  if (start == null || end == null) return true;
  const parts = localParts(now);
  const current = parts.hour * 60 + parts.minute;
  return current >= start && current <= end;
}

export function isBiometricWindowOpen(config: AttendanceConfigDto | null | undefined, now = new Date()): boolean {
  return isVerificationWindowOpen(config, now);
}

/** Daily verification window for fingerprint and manual code. */
export function isVerificationWindowOpen(
  config: AttendanceConfigDto | null | undefined,
  now = new Date(),
): boolean {
  if (!isBusinessDay(config, now)) return false;
  return isWithinTimeWindow(config, 'biometricWindowStart', 'biometricWindowEnd', now);
}

export function isSessionEnded(config: AttendanceConfigDto | null | undefined, now = new Date()): boolean {
  if (!isBusinessDay(config, now)) return false;
  const resolved = resolveSessionConfig(config);
  const end = parseTimeToMinutes(resolved.sessionEndTime);
  if (end == null) return false;
  const parts = localParts(now);
  const current = parts.hour * 60 + parts.minute;
  return current > end;
}

export const DEFAULT_VERIFICATION_REQUIRED_ROLES = ['class_teacher', 'subject_teacher'];

/**
 * Verification is opt-in. Role membership alone must not activate the
 * biometric gate for tenants that have not configured a usable window.
 * A session window is also accepted because biometric windows intentionally
 * default to the configured session window.
 */
export function hasVerificationWindowConfigured(
  config?: Pick<
    AttendanceConfigDto,
    | 'biometricWindowStart'
    | 'biometricWindowEnd'
    | 'sessionStartTime'
    | 'sessionEndTime'
  > | null,
): boolean {
  const biometricWindowConfigured =
    !!config?.biometricWindowStart && !!config?.biometricWindowEnd;
  const sessionWindowConfigured =
    !!config?.sessionStartTime && !!config?.sessionEndTime;
  return biometricWindowConfigured || sessionWindowConfigured;
}

export function resolveVerificationRequiredRoles(
  config?: { verificationRequiredRoles?: string[] } | null,
): string[] {
  if (
    Array.isArray(config?.verificationRequiredRoles) &&
    config.verificationRequiredRoles.length > 0
  ) {
    return config.verificationRequiredRoles
      .map((r) => String(r).trim().toLowerCase().replace(/[\s-]+/g, '_'))
      .filter(Boolean);
  }
  return DEFAULT_VERIFICATION_REQUIRED_ROLES;
}

export function userRequiresDailyVerification(
  userRoles: string[],
  config?: (Pick<
    AttendanceConfigDto,
    | 'biometricWindowStart'
    | 'biometricWindowEnd'
    | 'sessionStartTime'
    | 'sessionEndTime'
  > & {
    verificationRequiredRoles?: string[];
  }) | null,
): boolean {
  if (!hasVerificationWindowConfigured(config)) return false;
  if (
    userRoles.some(
      (role) => String(role).trim().toLowerCase().replace(/[\s-]+/g, '_') === 'director',
    )
  ) {
    return false;
  }
  const required = new Set(resolveVerificationRequiredRoles(config));
  return userRoles
    .map((r) => String(r).trim().toLowerCase().replace(/[\s-]+/g, '_'))
    .some((role) => required.has(role));
}

export function generateManualVerificationCode(): string {
  let code = '';
  for (let i = 0; i < 20; i++) {
    code += Math.floor(Math.random() * 10).toString();
  }
  return code;
}
