import { Injectable, Logger } from '@nestjs/common';
import { ProviderSecretsService } from '../provider-secrets.service';
import { AiProvider } from './provider.interfaces';

/**
 * OpenAI adapter — uses api_key from control vault when configured.
 * Falls back to a deterministic stub response when key is missing (fail closed on HTTP).
 */
@Injectable()
export class OpenAiGateway implements AiProvider {
  readonly id = 'openai';
  private readonly logger = new Logger(OpenAiGateway.name);

  constructor(private readonly secrets: ProviderSecretsService) {}

  private async apiKey(tenantId?: string): Promise<string> {
    if (tenantId) {
      try {
        const payload = await this.secrets.getSecrets(tenantId, 'ai');
        const key =
          payload?.secrets?.api_key ||
          payload?.secrets?.apiKey ||
          payload?.secrets?.OPENAI_API_KEY;
        if (key) return key;
      } catch {
        /* fall through */
      }
    }
    return process.env.OPENAI_API_KEY || '';
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
      this.logger.warn('OpenAI api_key missing');
      throw new Error('AI provider is not configured');
    }

    const model = input.model || process.env.OPENAI_MODEL || 'gpt-4o-mini';
    const res = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      signal: AbortSignal.timeout(12_000),
      headers: {
        Authorization: `Bearer ${key}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model,
        messages: input.messages,
        temperature: input.temperature ?? 0.7,
        max_tokens: input.maxTokens ?? 512,
      }),
    });

    if (!res.ok) {
      const text = await res.text();
      this.logger.warn(`OpenAI chat failed (${res.status}): ${text.slice(0, 200)}`);
      throw new Error(`OpenAI request failed (${res.status})`);
    }

    const json = (await res.json()) as any;
    const content = json?.choices?.[0]?.message?.content || '';
    return {
      content,
      usage: {
        promptTokens: json?.usage?.prompt_tokens,
        completionTokens: json?.usage?.completion_tokens,
      },
    };
  }
}
