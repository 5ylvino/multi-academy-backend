import { Injectable, InternalServerErrorException } from '@nestjs/common';
import { createCipheriv, createDecipheriv, randomBytes, createHash, createSecretKey, KeyObject } from 'crypto';

// AES-256-GCM with 12-byte IV. Output format: base64( iv || authTag || ciphertext )
@Injectable()
export class CryptoService {
  private key: KeyObject | null = null;

  /** True when ENCRYPTION_KEY is present (does not validate length/format). */
  isConfigured(): boolean {
    return Boolean(process.env.ENCRYPTION_KEY);
  }

  private ensureKey(): KeyObject {
    if (this.key) return this.key;

    const raw = process.env.ENCRYPTION_KEY || '';
    if (!raw) {
      throw new InternalServerErrorException(
        'ENCRYPTION_KEY is not configured. Set it in the production environment variables.',
      );
    }

    // Accept hex or base64; fallback to SHA-256 of raw string
    let key: Buffer;
    try {
      key = Buffer.from(raw, 'base64');
    } catch {
      key = Buffer.from([]);
    }
    if (key.length !== 32) {
      try {
        const k = Buffer.from(raw, 'hex');
        if (k.length === 32) key = k;
      } catch {
        // ignore
      }
    }
    if (key.length !== 32) {
      key = createHash('sha256').update(raw).digest();
    }
    this.key = createSecretKey(key as any);
    return this.key;
  }

  encrypt(plaintext: string): string {
    const key = this.ensureKey();
    const ivBuf = randomBytes(12);
    const iv = new Uint8Array(ivBuf.buffer, ivBuf.byteOffset, ivBuf.byteLength);
    const cipher = createCipheriv('aes-256-gcm', key as any, iv as any);
    const c: any = cipher;
    const ciphertext = Buffer.concat([c.update(plaintext, 'utf8'), c.final()]);
    const authTag = c.getAuthTag();
    return Buffer.concat([ivBuf, authTag, ciphertext]).toString('base64');
  }

  decrypt(payload: string): string {
    const key = this.ensureKey();
    const data = Buffer.from(payload, 'base64');
    const ivBuf = data.subarray(0, 12);
    const authTag = data.subarray(12, 28);
    const ciphertext = data.subarray(28);
    const iv = new Uint8Array(ivBuf.buffer, ivBuf.byteOffset, ivBuf.byteLength);
    const decipher = createDecipheriv('aes-256-gcm', key as any, iv as any);
    const d: any = decipher;
    d.setAuthTag(authTag);
    const plaintext = Buffer.concat([d.update(ciphertext), d.final()]);
    return plaintext.toString('utf8');
  }
}
