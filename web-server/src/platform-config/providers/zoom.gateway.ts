import { Injectable } from '@nestjs/common';
import { createHash, randomBytes } from 'crypto';
import { MeetingProvider } from './provider.interfaces';

/**
 * Zoom meeting adapter — proves Meet ↔ Zoom switch via control providerId.
 * Without live Zoom OAuth credentials, generates zoom.us-style join URLs (MVP).
 */
@Injectable()
export class ZoomGateway implements MeetingProvider {
  readonly id = 'zoom';

  async createMeeting(input: {
    title: string;
    startsAt: string;
    endsAt?: string;
    attendees?: string[];
  }): Promise<{ providerMeetingId: string; joinUrl: string; hostUrl?: string }> {
    const seed = `zoom:${input.title}:${input.startsAt}:${(input.attendees || []).join(',')}`;
    const digest = createHash('sha256').update(seed).digest('hex');
    const meetingNumber = String(parseInt(digest.slice(0, 10), 16) % 10_000_000_000).padStart(
      10,
      '0',
    );
    const pwd = digest.slice(10, 16);
    const providerMeetingId = `zoom_${randomBytes(6).toString('hex')}`;
    const joinUrl = `https://zoom.us/j/${meetingNumber}?pwd=${pwd}`;
    return {
      providerMeetingId,
      joinUrl,
      hostUrl: `https://zoom.us/s/${meetingNumber}?zak=stub`,
    };
  }

  async cancelMeeting(_providerMeetingId: string): Promise<void> {
    return;
  }
}
