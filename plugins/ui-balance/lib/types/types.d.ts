/**
 * Balance domain payloads shared by the node half (service) and the browser
 * half (capsule UI). Pure data — no runtime identity — so the browser bundle
 * may inline the type contract while the node half owns the values.
 * @module @deepseek-ai/dsh-client-ui-balance/types
 */
/** Provider shapes the balance service knows how to query. */
export type BalanceKind = 'deepseek' | 'custom';
/** One configured API entry (without the id the store assigns). */
export interface BalanceEntryInput {
    /** Display label shown in the capsule, e.g. "DeepSeek 主账户". */
    label: string;
    /** Built-in 'deepseek' endpoint logic, or 'custom' with an explicit balanceUrl. */
    kind: BalanceKind;
    /** API base URL; deepseek defaults to https://api.deepseek.com when absent. */
    baseUrl?: string;
    /** Full balance endpoint URL; required for kind 'custom'. */
    balanceUrl?: string;
    /** Inline API key (preferred over credential; never echoed back by list()). */
    apiKey?: string;
    /** Name of a credential reference (e.g. DEEPSEEK_API_KEY) resolved via ctx.credentials. */
    credential?: string;
}
/** One stored API entry. */
export interface BalanceEntry extends BalanceEntryInput {
    /** Stable store id; assigned by the node half on add. */
    id: string;
}
/** One currency bucket of a successful balance response. */
export interface BalanceInfo {
    /** ISO-ish currency code, e.g. "CNY". */
    currency: string;
    /** Formatted total balance as returned by the provider, e.g. "223.23". */
    total: string;
}
/** Outcome of one per-entry balance query. */
export interface BalanceResult {
    /** Entry id this result belongs to. */
    id: string;
    /** Entry label (snapshot at query time). */
    label: string;
    /** True when the provider answered and the response parsed. */
    ok: boolean;
    /** Provider-reported availability flag (deepseek: is_available). */
    available?: boolean;
    /** Parsed currency buckets; present when ok. */
    infos?: BalanceInfo[];
    /** Human-readable failure reason; present when !ok. */
    error?: string;
    /** Epoch millis of the query. */
    updatedAt: number;
}
/** On-disk store shape ($DSH_HOME/balance.json). */
export interface BalanceStoreFile {
    apis: BalanceEntry[];
}
//# sourceMappingURL=types.d.ts.map