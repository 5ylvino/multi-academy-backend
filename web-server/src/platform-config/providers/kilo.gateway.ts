import { Injectable, Logger } from '@nestjs/common';
import { ProviderSecretsService } from '../provider-secrets.service';
import { AiProvider } from './provider.interfaces';

const DEFAULT_BASE_URL = 'https://api.kilo.ai/api/gateway';
/** Sensible default; any OpenAI-compatible model id can override via request or vault settings. */
const DEFAULT_MODEL = 'nvidia/llama-3.1-nemotron-70b-instruct';

/**
 * Kilo AI Gateway adapter (OpenAI-compatible).
 * Base URL: https://api.kilo.ai/api/gateway
 * Vault: api_key; optional settings.model / settings.base_url for any custom model.
 */
@Injectable()
export class KiloGateway implements AiProvider {
  readonly id = 'kilo';
  private readonly logger = new Logger(KiloGateway.name);

  constructor(private readonly secrets: ProviderSecretsService) {}

  private async resolveConfig(tenantId?: string): Promise<{
    apiKey: string;
    baseUrl: string;
    defaultModel: string;
  }> {
    let apiKey = '';
    let baseUrl = process.env.KILO_BASE_URL || DEFAULT_BASE_URL;
    let defaultModel =
      process.env.KILO_MODEL || process.env.NVIDIA_MODEL || DEFAULT_MODEL;

    if (tenantId) {
      try {
        const payload = await this.secrets.getSecrets(tenantId, 'ai');
        const secrets = payload?.secrets || {};
        const settings = payload?.settings || {};
        apiKey =
          secrets.api_key ||
          secrets.apiKey ||
          secrets.KILO_API_KEY ||
          secrets.NVIDIA_API_KEY ||
          '';
        const settingsModel =
          (typeof settings.model === 'string' && settings.model.trim()) ||
          (typeof settings.default_model === 'string' &&
            settings.default_model.trim()) ||
          (typeof settings.defaultModel === 'string' &&
            settings.defaultModel.trim()) ||
          '';
        if (settingsModel) defaultModel = settingsModel;
        const settingsBase =
          (typeof settings.base_url === 'string' && settings.base_url.trim()) ||
          (typeof settings.baseUrl === 'string' && settings.baseUrl.trim()) ||
          '';
        if (settingsBase) baseUrl = settingsBase.replace(/\/$/, '');
      } catch {
        /* fall through to env */
      }
    }

    if (!apiKey) {
      apiKey = process.env.KILO_API_KEY || process.env.NVIDIA_API_KEY || '';
    }

    return { apiKey, baseUrl: baseUrl.replace(/\/$/, ''), defaultModel };
  }

  async chat(input: {
    messages: { role: string; content: string }[];
    model?: string;
    temperature?: number;
    maxTokens?: number;
    tenantId?: string;
  }): Promise<{ content: string; usage?: { promptTokens?: number; completionTokens?: number } }> {
    const tenantId = (input as { tenantId?: string }).tenantId;
    const { apiKey, baseUrl, defaultModel } = await this.resolveConfig(tenantId);

    if (!apiKey) {
      this.logger.warn('Kilo api_key missing');
      throw new Error('AI provider is not configured');
    }

    // Request model wins → vault/settings model → env → default (any custom id allowed).
    const model = (input.model || '').trim() || defaultModel;
    const url = `${baseUrl}/chat/completions`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
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
      this.logger.warn(
        `Kilo chat failed (${res.status}) model=${model}: ${text.slice(0, 200)}`,
      );
      throw new Error(`Kilo request failed (${res.status})`);
    }

    const json = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };
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
