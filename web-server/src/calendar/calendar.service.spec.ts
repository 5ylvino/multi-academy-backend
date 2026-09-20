import { canViewCalendarAudience } from './calendar.service';

describe('calendar audience policy', () => {
  it('keeps parent and student events role-relative', () => {
    expect(canViewCalendarAudience('parents', ['parent'])).toBe(true);
    expect(canViewCalendarAudience('parents', ['student'])).toBe(false);
    expect(canViewCalendarAudience('students', ['student'])).toBe(true);
    expect(canViewCalendarAudience('students', ['parent'])).toBe(false);
  });

  it('allows staff and platform administrators to see staff events', () => {
    expect(canViewCalendarAudience('staff', ['subject_teacher'])).toBe(true);
    expect(canViewCalendarAudience('staff', ['parent'])).toBe(false);
    expect(canViewCalendarAudience('staff', ['school_admin'])).toBe(true);
    expect(canViewCalendarAudience('all', ['parent'])).toBe(true);
  });
});
