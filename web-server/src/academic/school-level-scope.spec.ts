import { schoolLevelMatches } from './academic.service';
import { hasAcademicManagementRole } from '../common/auth/teacher-scope.util';

describe('schoolLevelMatches', () => {
  it('matches secondary users to JSS and SSS records', () => {
    expect(schoolLevelMatches('secondary', 'jss')).toBe(true);
    expect(schoolLevelMatches('secondary', 'sss')).toBe(true);
  });

  it('does not cross primary and secondary scopes', () => {
    expect(schoolLevelMatches('primary', 'secondary')).toBe(false);
    expect(schoolLevelMatches('secondary', 'primary')).toBe(false);
  });

  it('recognizes management access when combined with a teacher role', () => {
    expect(hasAcademicManagementRole(['subject_teacher', 'school_admin'])).toBe(true);
    expect(hasAcademicManagementRole(['subject_teacher'])).toBe(false);
  });
});
