import { canAccessNursery } from './nursery.service';

describe('nursery role access', () => {
  it.each(['class_teacher', 'head_teacher', 'principal', 'school_admin'])(
    'allows %s',
    (role) => {
      expect(canAccessNursery([role])).toBe(true);
    },
  );

  it.each([
    'director',
    'it_admin',
    'subject_teacher',
    'administrative_staff',
    'bursar',
    'parent',
    'student',
  ])('denies unrelated role %s', (role) => {
    expect(canAccessNursery([role])).toBe(false);
  });
});
