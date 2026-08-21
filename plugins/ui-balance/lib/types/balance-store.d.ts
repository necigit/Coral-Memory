/**
 * Node-side store and provider-query logic for the balance capsule. Pure
 * Node: file persistence under $DSH_HOME plus HTTP balance queries with the
 * credential seam for key resolution. Nothing here is browser-reachable —
 * the browser half only ever sees the Remote results.
 * @module @deepseek-ai/dsh-client-ui-balance/src/balance-store
 */
import type { CredentialProvider } from '@deepseek-ai/dsh-credentials';
import type { BalanceEntry, BalanceInfo, BalanceResult, BalanceStoreFile } from './types.ts';
/** Default DeepSeek API base. */
export declare const DEEPSEEK_BASE_URL = "https://api.deepseek.com";
/** Resolve the persistent store file (overridable for tests). */
export declare function balanceStorePath(file?: string): string;
/** Read the store; returns null when absent or malformed. */
export declare function readStore(file?: string): Promise<BalanceStoreFile | null>;
/** Write the store, creating its directory when needed. */
export declare function writeStore(store: BalanceStoreFile, file?: string): Promise<void>;
/**
 * Resolve an entry's key: inline apiKey wins, then the named credential
 * (env first, then the credentials seam).
 * @param entry - stored entry.
 * @param credentials - optional credentials service (absent in bare contexts).
 * @returns the key, or undefined when nothing is configured.
 */
export declare function resolveApiKey(entry: BalanceEntry, credentials: CredentialProvider | undefined): Promise<string | undefined>;
/**
 * Default seed: one deepseek entry bound to the DEEPSEEK_API_KEY credential,
 * only when that credential actually resolves (so a fresh install with no key
 * yields an empty store instead of a dead row).
 * @param credentials - optional credentials service.
 * @returns the seeded entries (empty when no key exists).
 */
export declare function seedDefault(credentials: CredentialProvider | undefined): Promise<BalanceEntry[]>;
/** Load the store, seeding defaults on first run (persisted only when a seed exists). */
export declare function loadStore(credentials: CredentialProvider | undefined, file?: string): Promise<BalanceEntry[]>;
/** Strip inline keys before anything crosses the Remote boundary. */
export declare function stripSecrets(entry: BalanceEntry): BalanceEntry;
/** Parse a provider balance response body into currency buckets. */
export declare function parseBalanceInfos(body: Record<string, unknown>): BalanceInfo[] | undefined;
/**
 * Query one entry and reduce the outcome to a BalanceResult. Never throws:
 * every failure becomes an !ok result so one broken API cannot kill the list.
 * @param entry - stored entry.
 * @param key - resolved API key (undefined → configuration error).
 * @returns the per-entry outcome.
 */
export declare function queryBalance(entry: BalanceEntry, key: string | undefined): Promise<BalanceResult>;
//# sourceMappingURL=balance-store.d.ts.map