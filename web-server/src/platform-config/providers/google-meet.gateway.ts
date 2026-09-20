import { Injectable } from '@nestjs/common';
import { createHash, randomBytes } from 'crypto';
import { MeetingProvider } from './provider.interfaces';

/**
 * Google Meet adapter — creates join URLs. Without live Google Calendar API
 * credentials, generates a deterministic meet.google.com-style code (MVP proof).
 * Control selects providerId=google_meet; school domain stays on MeetingProvider port.
 */
@Injectable()
export class GoogleMeetGateway implements MeetingProvider {
  readonly id = 'google_meet';

  async createMeeting(input: {
    title: string;
    startsAt: string;
    endsAt?: string;
    attendees?: string[];
  }): Promise<{ providerMeetingId: string; joinUrl: string; hostUrl?: string }> {
    const seed = `${input.title}:${input.startsAt}:${(input.attendees || []).join(',')}`;
    const digest = createHash('sha256').update(seed).digest('hex');
    const code = `${digest.slice(0, 3)}-${digest.slice(3, 7)}-${digest.slice(7, 10)}`;
    const providerMeetingId = `gmeet_${randomBytes(6).toString('hex')}`;
    const joinUrl = `https://meet.google.com/${code}`;
    return {
      providerMeetingId,
      joinUrl,
      hostUrl: joinUrl,
    };
  }

  async cancelMeeting(_providerMeetingId: string): Promise<void> {
    return;
  }
}
