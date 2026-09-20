import { canJoinMeeting, canViewMeeting } from './meetings.service';

describe('meeting visibility', () => {
  it('limits meetings to the host or explicit attendees', () => {
    const meeting = { createdBy: 'staff-1', attendeeIds: ['parent-1'] };
    expect(canViewMeeting(meeting, 'staff-1')).toBe(true);
    expect(canViewMeeting(meeting, 'parent-1')).toBe(true);
    expect(canViewMeeting(meeting, 'parent-2')).toBe(false);
  });

  it('lets enrolled class members join a live class they were not listed on', () => {
    const meeting = { createdBy: 'staff-1', attendeeIds: [], classId: 'class-9' };
    expect(canJoinMeeting(meeting, 'student-1', new Set(['class-9']))).toBe(true);
    expect(canJoinMeeting(meeting, 'student-2', new Set(['class-3']))).toBe(false);
  });
});
