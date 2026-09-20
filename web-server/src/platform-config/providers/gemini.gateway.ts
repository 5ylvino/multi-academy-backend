import { Injectable, Logger } from '@nestjs/common';
import { ProviderSecretsService } from '../provider-secrets.service';
import { AiProvider } from './provider.interfaces';

/**
 * Google Gemini adapter — uses api_key from control vault (ai capability).
 * Mirrors OpenAI gateway: stub reply when key missing in local/dev; throws on HTTP failure.
 */
@Injectable()
export class GeminiGateway implements AiProvider {
  readonly id = 'gemini';
  private readonly logger = new Logger(GeminiGateway.name);

  constructor(private readonly secrets: ProviderSecretsService) {}

  private async apiKey(tenantId?: string): Promise<string> {
    if (tenantId) {
      try {
        const payload = await this.secrets.getSecrets(tenantId, 'ai');
        const key =
          payload?.secrets?.api_key ||
          payload?.secrets?.apiKey ||
          payload?.secrets?.GEMINI_API_KEY;
        if (key) return key;
      } catch {
        /* fall through */
      }
    }
    return process.env.GEMINI_API_KEY || process.env.GOOGLE_AI_API_KEY || '';
  }

  async chat(input: {
    messages: { role: string; content: string }[];
    model?: string;
    temperature?: number;
    maxTokens?: number;
    tenantId?: string;
  }): Promise<{ content: string; usage?: { promptTokens?: number; completionTokens?: number } }> {
    const tenantId = (input as any).tenantId as string | undefined;
    const key = await this.apiKey(tenantId);
    if (!key) {
      this.logger.warn('Gemini api_key missing');
      throw new Error('AI provider is not configured');
    }

    const model =
      input.model || process.env.GEMINI_MODEL || 'gemini-1.5-flash';
    const systemParts = input.messages
      .filter((m) => m.role === 'system')
      .map((m) => m.content)
      .filter(Boolean);
    const contents = input.messages
      .filter((m) => m.role === 'user' || m.role === 'assistant')
      .map((m) => ({
        role: m.role === 'assistant' ? 'model' : 'user',
        parts: [{ text: m.content }],
      }));

    const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(key)}`;
    const body: Record<string, unknown> = {
      contents,
      generationConfig: {
        temperature: input.temperature ?? 0.7,
        maxOutputTokens: input.maxTokens ?? 512,
      },
    };
    if (systemParts.length) {
      body.systemInstruction = { parts: [{ text: systemParts.join('\n') }] };
    }

    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`Gemini chat failed (${res.status}): ${text.slice(0, 200)}`);
      throw new Error(`Gemini request failed (${res.status})`);
    }

    const json = (await res.json()) as any;
    const content =
      json?.candidates?.[0]?.content?.parts?.map((p: any) => p?.text || '').join('') ||
      '';
    const usageMeta = json?.usageMetadata || {};
    return {
      content,
      usage: {
        promptTokens: usageMeta.promptTokenCount,
        completionTokens: usageMeta.candidatesTokenCount,
      },
    };
  }
}
