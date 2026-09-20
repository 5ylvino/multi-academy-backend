import { isMessagingAudienceAllowed } from './messaging.service';

describe('messaging audience policy', () => {
  it('allows parents to message teachers but not other parents', () => {
    expect(
      isMessagingAudienceAllowed(['parent'], [['subject_teacher']]).allowed,
    ).toBe(true);
    expect(isMessagingAudienceAllowed(['parent'], [['parent']])).toEqual({
      allowed: false,
      reason: 'Parents may message school staff only',
    });
  });

  it('allows teachers to message parents but not unrelated staff', () => {
    expect(
      isMessagingAudienceAllowed(['class_teacher'], [['parent']]).allowed,
    ).toBe(true);
    expect(
      isMessagingAudienceAllowed(['subject_teacher'], [['bursar']]),
    ).toEqual({
      allowed: false,
      reason: 'Teachers may message parents only',
    });
  });

  it('allows administrative roles to coordinate broadly', () => {
    expect(
      isMessagingAudienceAllowed(['school_admin'], [['parent'], ['teacher']])
        .allowed,
    ).toBe(true);
  });
});
