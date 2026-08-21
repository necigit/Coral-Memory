/**
 * API balance capsule, node half. Hosts the `balance` Typert Remote service:
 * a persistent multi-API entry store ($DSH_HOME/balance.json) plus per-entry
 * balance queries over plain fetch. Keys never cross the Remote boundary —
 * list() strips inline keys, and resolution happens per query through the
 * credentials seam / environment. The browser half only ever receives
 * BalanceEntry (sans secrets) and BalanceResult payloads, so the capsule
 * costs zero model tokens: no agent, no context, no session involvement.
 * @module @deepseek-ai/dsh-client-ui-balance
 */
import type { Context } from '@deepseek-ai/cordis';
import { TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol';
import type { BalanceEntry, BalanceEntryInput, BalanceResult } from './types.ts';
export type { BalanceEntry, BalanceEntryInput, BalanceInfo, BalanceKind, BalanceResult, BalanceStoreFile, } from './types.ts';
/** The `balance` Remote service: store CRUD + query, all session-free. */
export declare class BalanceService extends TypertRemoteService {
    constructor(ctx: Context);
    /** Optional credential seam; absent in bare contexts (env fallback still applies). */
    private credentials;
    /** Current entries, seeding the default DeepSeek row on first run. */
    private load;
    /** List configured entries; inline keys are never echoed. */
    list(): Promise<BalanceEntry[]>;
    /** Query every configured entry and return per-entry outcomes. */
    query(): Promise<BalanceResult[]>;
    /** Add one API entry and return the fresh (secrets-stripped) list. */
    add(entry: BalanceEntryInput): Promise<BalanceEntry[]>;
    /** Remove one API entry and return the fresh list. */
    remove(id: string): Promise<BalanceEntry[]>;
    /** Patch one API entry and return the fresh list. */
    update(id: string, patch: Partial<BalanceEntryInput>): Promise<BalanceEntry[]>;
}
/** Host plugin body: mount the service. */
export declare function apply(ctx: Context): void;
export default BalanceService;
//# sourceMappingURL=index.d.ts.map