import { Injectable } from '@nestjs/common';
import { randomBytes, scryptSync, timingSafeEqual } from 'crypto';

// Format: scrypt$N$r$p$saltBase64$hashBase64
// Where N is cost (log2), r and p are block/parallelization params
const DEFAULT_N_LOG2 = 14; // 2^14 (reduce memory/CPU while staying strong)
const DEFAULT_R = 8;
const DEFAULT_P = 1;
const KEY_LEN = 32; // 256-bit
const MAX_MEM = 64 * 1024 * 1024; // 64MB upper bound to avoid memory errors

@Injectable()
export class PasswordService {
  hashPassword(plaintext: string): string {
    const salt = randomBytes(16).toString('hex'); // 32 hex chars
    const N = 1 << DEFAULT_N_LOG2;
    const derived = scryptSync(plaintext, salt, KEY_LEN, { N, r: DEFAULT_R, p: DEFAULT_P, maxmem: MAX_MEM }) as Buffer;
    return [
      'scrypt',
      DEFAULT_N_LOG2.toString(),
      DEFAULT_R.toString(),
      DEFAULT_P.toString(),
      salt, // hex
      derived.toString('base64'),
    ].join('$');
  }

  verifyPassword(plaintext: string, encoded: string): boolean {
    const parts = encoded.split('$');
    if (parts.length !== 6 || parts[0] !== 'scrypt') return false;
    const Nlog2 = parseInt(parts[1], 10);
    const r = parseInt(parts[2], 10);
    const p = parseInt(parts[3], 10);
    const salt = parts[4]; // hex
    const hash = Buffer.from(parts[5], 'base64');
    const N = 1 << Nlog2;
    const derived = scryptSync(plaintext, salt, hash.length, { N, r, p, maxmem: MAX_MEM }) as Buffer;
    return timingSafeEqual(hash as any, derived as any);
  }
}

