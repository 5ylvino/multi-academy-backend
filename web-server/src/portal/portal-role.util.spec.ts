import { hasPortalRole, normalizePortalRoles } from './portal-role.util';

describe('portal role ownership', () => {
  it('normalizes legacy and JSON role formats', () => {
    expect(normalizePortalRoles('Parent', '["teacher", "parent"]')).toEqual([
      'teacher',
      'parent',
      'parent',
    ]);
    expect(normalizePortalRoles('student', null)).toEqual(['student']);
  });

  it('requires the exact self-service role', () => {
    expect(hasPortalRole('school_admin', '["admin"]', 'parent')).toBe(false);
    expect(hasPortalRole('admin', '["parent"]', 'parent')).toBe(true);
    expect(hasPortalRole('school-admin', '["student"]', 'student')).toBe(true);
    expect(hasPortalRole('director', '["student"]', 'parent')).toBe(false);
  });

  it('keeps combined parent/student entry points role-specific', () => {
    const roles = '["parent", "student"]';

    expect(hasPortalRole('parent', roles, 'parent')).toBe(true);
    expect(hasPortalRole('parent', roles, 'student')).toBe(true);
    expect(normalizePortalRoles('parent', roles)).not.toContain('teacher');
  });
});
