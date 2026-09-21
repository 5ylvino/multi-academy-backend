import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createHmac, randomUUID } from 'crypto';

type ServiceJwtPayload = {
  iss: string;
  aud: string;
  sub: string;
  tenant_id: string;
  actor_id: string;
  roles: string[];
  features: string[];
  iat: number;
  exp: number;
  jti: string;
};

export type AiServiceActor = {
  userId: string;
  roles?: string[];
  features?: string[];
};

@Injectable()
export class AiServiceClient {
  private readonly logger = new Logger(AiServiceClient.name);

  constructor(private readonly config: ConfigService) {}

  isEnabled(): boolean {
    return Boolean(this.baseUrl());
  }

  private baseUrl(): string {
    return (this.config.get<string>('AI_SERVICE_URL') || '').replace(/\/$/, '');
  }

  private jwtSecret(): string {
    return (
      this.config.get<string>('AI_SERVICE_JWT_SECRET') ||
      this.config.get<string>('SERVICE_JWT_SECRET') ||
      'change-me'
    );
  }

  private jwtIssuer(): string {
    return this.config.get<string>('AI_SERVICE_JWT_ISSUER') || 'mas-school-server';
  }

  private jwtAudience(): string {
    return this.config.get<string>('AI_SERVICE_JWT_AUDIENCE') || 'mas-ai-service';
  }

  private base64url(value: string): string {
    return Buffer.from(value, 'utf8').toString('base64url');
  }

  private signServiceJwt(payload: ServiceJwtPayload): string {
    const header = this.base64url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
    const body = this.base64url(JSON.stringify(payload));
    const signingInput = `${header}.${body}`;
    const signature = createHmac('sha256', this.jwtSecret()).update(signingInput).digest('base64url');
    return `${signingInput}.${signature}`;
  }

  issueServiceToken(
    tenantId: string,
    actor: AiServiceActor,
    options?: { features?: string[]; ttlSeconds?: number },
  ): string {
    const now = Math.floor(Date.now() / 1000);
    const ttl = options?.ttlSeconds ?? 300;
    const payload: ServiceJwtPayload = {
      iss: this.jwtIssuer(),
      aud: this.jwtAudience(),
      sub: 'service',
      tenant_id: tenantId,
      actor_id: actor.userId,
      roles: (actor.roles || []).map((r) => String(r).toLowerCase()),
      features: options?.features || actor.features || [],
      iat: now,
      exp: now + ttl,
      jti: randomUUID(),
    };
    return this.signServiceJwt(payload);
  }

  async pingLlm(tenantId: string, actor: AiServiceActor): Promise<{ content?: string; providerId?: string; model?: string }> {
    if (!this.isEnabled()) {
      throw new Error('AI service URL is not configured (AI_SERVICE_URL)');
    }
    const token = this.issueServiceToken(tenantId, actor, { features: ['ai.assistant'] });
    const url = `${this.baseUrl()}/v1/internal/llm/ping`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'X-Tenant-Id': tenantId,
        'X-Request-Id': randomUUID(),
      },
    });
    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`AI service ping failed (${res.status}): ${text.slice(0, 200)}`);
      throw new Error(`AI service ping failed (${res.status})`);
    }
    return (await res.json()) as { content?: string; providerId?: string; model?: string };
  }

  async assistantChat(
    tenantId: string,
    actor: AiServiceActor,
    body: {
      messages: { role: string; content: string }[];
      model?: string;
      schoolContext?: unknown;
      provider?: { providerId?: string; model?: string; maxTokens?: number };
    },
  ) {
    return this.post<{
      content?: string;
      providerId?: string;
      model?: string;
      usage?: { promptTokens?: number; completionTokens?: number };
      sources?: unknown[];
    }>('/v1/assistant/chat', tenantId, actor, body, { features: ['ai.assistant'] });
  }

  async supportChat(
    tenantId: string,
    actor: AiServiceActor,
    body: {
      messages: { role: string; content: string }[];
      model?: string;
      schoolContext?: unknown;
      provider?: { providerId?: string; model?: string; maxTokens?: number };
    },
  ) {
    return this.post<{
      content?: string;
      providerId?: string;
      model?: string;
      usage?: { promptTokens?: number; completionTokens?: number };
      sources?: unknown[];
    }>('/v1/support/chat', tenantId, actor, body, { features: ['ai.support_chatbot'] });
  }

  async tutorChat(
    tenantId: string,
    actor: AiServiceActor,
    body: {
      message: string;
      sessionId?: string;
      subjectId?: string;
      topic?: string;
      schoolContext?: unknown;
      provider?: { providerId?: string; model?: string; maxTokens?: number };
    },
  ) {
    return this.post<{
      sessionId?: string;
      content?: string;
      state?: string;
      masteryPct?: number;
      quiz?: unknown;
      providerId?: string;
      model?: string;
      disclaimer?: string;
      sources?: unknown[];
    }>('/v1/tutor/chat', tenantId, actor, body, { features: ['ai.tutor'] });
  }

  async tutorQuizAnswer(
    tenantId: string,
    actor: AiServiceActor,
    body: { sessionId: string; answer: string },
  ) {
    return this.post<{
      sessionId?: string;
      content?: string;
      state?: string;
      masteryPct?: number;
      providerId?: string;
      model?: string;
      disclaimer?: string;
    }>('/v1/tutor/quiz/answer', tenantId, actor, body, { features: ['ai.tutor'] });
  }

  async tutorMastery(tenantId: string, actor: AiServiceActor, studentId: string) {
    return this.get<{ studentId?: string; items?: unknown[] }>(
      `/v1/tutor/mastery?studentId=${encodeURIComponent(studentId)}`,
      tenantId,
      actor,
      { features: ['ai.tutor'] },
    );
  }

  async guidanceHome(
    tenantId: string,
    actor: AiServiceActor,
    body: {
      studentId: string;
      question?: string;
      studentSignals?: unknown;
      provider?: { providerId?: string; model?: string; maxTokens?: number };
    },
  ) {
    return this.post<{
      studentId?: string;
      topic?: string;
      subject?: string;
      resources?: unknown[];
      tonightChecklist?: string[];
      atHome?: string[];
      sources?: string[];
      disclaimer?: string;
    }>('/v1/guidance/home', tenantId, actor, body, { features: ['ai.performance_recommendations'] });
  }

  async insightsDetect(
    tenantId: string,
    actor: AiServiceActor,
    body: {
      classId: string;
      subjectId?: string;
      masteryThreshold?: number;
      studentMastery?: unknown[];
      provider?: { providerId?: string; model?: string; maxTokens?: number };
    },
  ) {
    return this.post<{
      classId?: string;
      heatmap?: unknown[];
      flags?: unknown[];
      narration?: string;
      disclaimer?: string;
    }>('/v1/insights/detect', tenantId, actor, body, { features: ['ai.performance_detection'] });
  }

  async insightsAtRisk(
    tenantId: string,
    actor: AiServiceActor,
    body: { classId?: string; students?: unknown[]; provider?: { providerId?: string; model?: string } },
  ) {
    return this.post<{
      count?: number;
      students?: unknown[];
      summary?: string;
      disclaimer?: string;
    }>('/v1/insights/at-risk', tenantId, actor, body, { features: ['ai.risk_analytics'] });
  }

  async copilotIntervention(
    tenantId: string,
    actor: AiServiceActor,
    body: {
      classId: string;
      subjectId?: string;
      topic: string;
      className?: string;
      subjectName?: string;
      weakTopic?: unknown;
      provider?: { providerId?: string; model?: string; maxTokens?: number };
    },
  ) {
    return this.post<{
      planId?: string;
      status?: string;
      classId?: string;
      topic?: string;
      plan?: unknown;
      disclaimer?: string;
    }>('/v1/copilot/intervention', tenantId, actor, body, { features: ['ai.teacher_copilot'] });
  }

  async copilotGetIntervention(tenantId: string, actor: AiServiceActor, planId: string) {
    return this.get<{ planId?: string; status?: string; plan?: unknown; topic?: string }>(
      `/v1/copilot/intervention/${encodeURIComponent(planId)}`,
      tenantId,
      actor,
      { features: ['ai.teacher_copilot'] },
    );
  }

  async copilotListInterventions(tenantId: string, actor: AiServiceActor, status?: string) {
    const q = status ? `?status=${encodeURIComponent(status)}` : '';
    return this.get<{ items?: unknown[] }>(
      `/v1/copilot/interventions${q}`,
      tenantId,
      actor,
      { features: ['ai.teacher_copilot'] },
    );
  }

  async copilotReviewIntervention(
    tenantId: string,
    actor: AiServiceActor,
    planId: string,
    body: { action: 'approve' | 'reject' },
  ) {
    return this.post<{ planId?: string; status?: string; plan?: unknown }>(
      `/v1/copilot/intervention/${encodeURIComponent(planId)}/review`,
      tenantId,
      actor,
      body,
      { features: ['ai.teacher_copilot'] },
    );
  }

  async reportCommentDraft(
    tenantId: string,
    actor: AiServiceActor,
    body: {
      studentId: string;
      termId?: string;
      subjectId?: string;
      studentName?: string;
      performanceSummary?: string;
      locale?: string;
    },
  ) {
    return this.post<{
      draftId?: string;
      commentText?: string;
      status?: string;
      disclaimer?: string;
    }>('/v1/reports/comment-draft', tenantId, actor, body, { features: ['ai.report_comments'] });
  }

  async timetableSolve(
    tenantId: string,
    actor: AiServiceActor,
    body: {
      classId: string;
      classSlots: unknown[];
      allSlots: unknown[];
      explain?: boolean;
    },
  ) {
    return this.post<{
      classId?: string;
      slotCount?: number;
      clashCount?: number;
      solver?: string;
      suggestions?: Array<{
        slotId: string;
        clashCount: number;
        currentDay: number;
        currentStart: string;
        currentEnd: string;
        proposedDay?: number;
        proposedStart?: string;
        proposedEnd?: string;
        suggestion: string;
        explanation?: string;
      }>;
      disclaimer?: string;
    }>('/v1/timetable/solve', tenantId, actor, body, { features: ['ai.timetable_solver'] });
  }

  async jambReadiness(tenantId: string, actor: AiServiceActor, studentId: string, studentSignals?: unknown) {
    return this.post<{
      studentId?: string;
      subjects?: Array<{ subjectName?: string; readinessPct?: number; attemptCount?: number }>;
      summary?: string;
      disclaimer?: string;
    }>('/v1/readiness/jamb', tenantId, actor, { studentId, studentSignals }, { features: ['academic.jamb_cbt'] });
  }

  async ingestScheme(tenantId: string, actor: AiServiceActor, body: { items: unknown[] }) {
    return this.post<{ ingested?: number; failed?: number; documentIds?: string[] }>(
      '/v1/ingest/scheme',
      tenantId,
      actor,
      body,
      { features: ['ai.assistant'] },
    );
  }

  async guidanceStudyPlan(
    tenantId: string,
    actor: AiServiceActor,
    body: {
      studentSignals?: unknown;
      provider?: { providerId?: string; model?: string; maxTokens?: number };
    },
  ) {
    return this.post<{
      studentId?: string;
      actions?: string[];
      focusTopics?: string[];
      masterySummary?: unknown[];
      sources?: string[];
      sourceMode?: string;
      disclaimer?: string;
    }>('/v1/guidance/study-plan', tenantId, actor, body, { features: ['ai.performance_recommendations'] });
  }

  async get<T>(
    path: string,
    tenantId: string,
    actor: AiServiceActor,
    options?: { features?: string[] },
  ): Promise<T> {
    if (!this.isEnabled()) {
      throw new Error('AI service URL is not configured (AI_SERVICE_URL)');
    }
    const token = this.issueServiceToken(tenantId, actor, { features: options?.features });
    const url = `${this.baseUrl()}${path.startsWith('/') ? path : `/${path}`}`;
    const res = await fetch(url, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'X-Tenant-Id': tenantId,
        'X-Request-Id': randomUUID(),
      },
    });
    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`AI service GET failed (${res.status}) ${path}: ${text.slice(0, 200)}`);
      throw new Error(`AI service request failed (${res.status})`);
    }
    return (await res.json()) as T;
  }

  async gradeEssay(
    tenantId: string,
    actor: AiServiceActor,
    body: {
      essayText: string;
      rubric?: string;
      maxScore?: number;
      model?: string;
      provider?: { providerId?: string; model?: string; maxTokens?: number };
    },
  ) {
    return this.post<{
      content?: string;
      providerId?: string;
      model?: string;
      score?: number;
      feedback?: string;
      usage?: { promptTokens?: number; completionTokens?: number };
    }>('/v1/essay/grade', tenantId, actor, body, { features: ['ai.essay_grading'] });
  }

  async post<T>(
    path: string,
    tenantId: string,
    actor: AiServiceActor,
    body: unknown,
    options?: { features?: string[] },
  ): Promise<T> {
    if (!this.isEnabled()) {
      throw new Error('AI service URL is not configured (AI_SERVICE_URL)');
    }
    const token = this.issueServiceToken(tenantId, actor, { features: options?.features });
    const url = `${this.baseUrl()}${path.startsWith('/') ? path : `/${path}`}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'X-Tenant-Id': tenantId,
        'X-Request-Id': randomUUID(),
      },
      body: JSON.stringify(body ?? {}),
    });
    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`AI service request failed (${res.status}) ${path}: ${text.slice(0, 200)}`);
      throw new Error(`AI service request failed (${res.status})`);
    }
    return (await res.json()) as T;
  }
}
