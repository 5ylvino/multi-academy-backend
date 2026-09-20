import { Injectable } from '@nestjs/common';
import { AiProvider } from './provider.interfaces';

/**
 * FAQ / calendar / fees Q&A skeleton under ai.assistant.
 * Replace with OpenAI/Gemini adapter when control selects a live AI provider.
 */
@Injectable()
export class StubAiGateway implements AiProvider {
  readonly id = 'stub_faq';

  async chat(input: {
    messages: { role: string; content: string }[];
    model?: string;
    temperature?: number;
    maxTokens?: number;
  }): Promise<{ content: string; usage?: { promptTokens?: number; completionTokens?: number } }> {
    const last = [...input.messages].reverse().find((m) => m.role === 'user')?.content || '';
    const q = last.toLowerCase();
    let content =
      'I am the school ops assistant (skeleton). Ask about fees, calendar, or attendance.';
    if (q.includes('fee') || q.includes('pay') || q.includes('invoice')) {
      content =
        'Fee payments: open Parent portal → Fees, or Financial → Invoices. Online checkout uses the active payment provider from the control plane (Paystack/Flutterwave).';
    } else if (q.includes('calendar') || q.includes('event') || q.includes('holiday')) {
      content =
        'School calendar: Dashboard → Calendar (comms.calendar). Staff can add holidays, exams, and PTA events.';
    } else if (q.includes('absent') || q.includes('attendance')) {
      content =
        'Attendance: staff mark students daily. Parents receive absence alerts when the school enables attendance.absence_alerts.';
    } else if (q.includes('report') || q.includes('result')) {
      content =
        'Report cards: Academic → Report cards (academic.report_cards). Ranking uses academic.term_ranking.';
    }
    return {
      content,
      usage: { promptTokens: Math.ceil(last.length / 4), completionTokens: Math.ceil(content.length / 4) },
    };
  }
}
