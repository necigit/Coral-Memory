/**
 * Browser half of the balance Remote contract: the strict wire descriptors
 * (hand-written twin of what the typert generator would emit) plus the typed
 * client face. The client bundle `$mount`s this contribution onto
 * `ctx.remote`, so `ctx.remote.balance.list()` etc. dispatch over the
 * connection carrier to the node half's BalanceService.
 * @module @deepseek-ai/dsh-client-ui-balance/src/client/remote
 */
import { z } from 'zod';
import type { RemoteResult } from '@deepseek-ai/dsh-typert-protocol';
import type { BalanceEntry, BalanceEntryInput, BalanceResult } from '../types.ts';
/** Strict wire schema for one stored entry (inline keys never arrive). */
export declare const balanceEntrySchema: z.ZodObject<{
    id: z.ZodString;
    label: z.ZodString;
    kind: z.ZodUnion<readonly [z.ZodLiteral<"deepseek">, z.ZodLiteral<"custom">]>;
    baseUrl: z.ZodOptional<z.ZodString>;
    balanceUrl: z.ZodOptional<z.ZodString>;
    apiKey: z.ZodOptional<z.ZodString>;
    credential: z.ZodOptional<z.ZodString>;
}, z.core.$strip>;
/** Strict wire schema for add() input. */
export declare const balanceEntryInputSchema: z.ZodObject<{
    apiKey: z.ZodOptional<z.ZodString>;
    label: z.ZodString;
    baseUrl: z.ZodOptional<z.ZodString>;
    kind: z.ZodUnion<readonly [z.ZodLiteral<"deepseek">, z.ZodLiteral<"custom">]>;
    balanceUrl: z.ZodOptional<z.ZodString>;
    credential: z.ZodOptional<z.ZodString>;
}, z.core.$strip>;
/** Strict wire schema for update() patches. */
export declare const balanceEntryPatchSchema: z.ZodObject<{
    label: z.ZodOptional<z.ZodString>;
    kind: z.ZodOptional<z.ZodUnion<readonly [z.ZodLiteral<"deepseek">, z.ZodLiteral<"custom">]>>;
    baseUrl: z.ZodOptional<z.ZodString>;
    balanceUrl: z.ZodOptional<z.ZodString>;
    apiKey: z.ZodOptional<z.ZodString>;
    credential: z.ZodOptional<z.ZodString>;
}, z.core.$strip>;
/** Strict wire schema for one currency bucket. */
export declare const balanceInfoSchema: z.ZodObject<{
    currency: z.ZodString;
    total: z.ZodString;
}, z.core.$strip>;
/** Strict wire schema for one query outcome. */
export declare const balanceResultSchema: z.ZodObject<{
    id: z.ZodString;
    label: z.ZodString;
    ok: z.ZodBoolean;
    available: z.ZodOptional<z.ZodBoolean>;
    infos: z.ZodOptional<z.ZodArray<z.ZodObject<{
        currency: z.ZodString;
        total: z.ZodString;
    }, z.core.$strip>>>;
    error: z.ZodOptional<z.ZodString>;
    updatedAt: z.ZodNumber;
}, z.core.$strip>;
/** Typed client face of the mounted `balance` Remote namespace. */
export interface BalanceRemoteApi {
    list(): Promise<RemoteResult<BalanceEntry[]>>;
    query(): Promise<RemoteResult<BalanceResult[]>>;
    add(entry: BalanceEntryInput): Promise<RemoteResult<BalanceEntry[]>>;
    remove(id: string): Promise<RemoteResult<BalanceEntry[]>>;
    update(id: string, patch: Partial<BalanceEntryInput>): Promise<RemoteResult<BalanceEntry[]>>;
}
/** Generated-shape Remote contribution consumed by `ctx.remote.$mount`. */
export declare const TYPERT_REMOTE: {
    readonly package: "@deepseek-ai/dsh-client-ui-balance";
    readonly descriptors: readonly [{
        readonly id: "@deepseek-ai/dsh-client-ui-balance#balance/list";
        readonly service: "balance";
        readonly namespace: "balance";
        readonly method: "list";
        readonly invocation: {
            readonly kind: "direct";
        };
        readonly parameters: readonly [];
        readonly result: {
            readonly mode: "strict";
            readonly typeSymbol: "@deepseek-ai/dsh-client-ui-balance/types#BalanceEntry[]";
            readonly schema: z.ZodArray<z.ZodObject<{
                id: z.ZodString;
                label: z.ZodString;
                kind: z.ZodUnion<readonly [z.ZodLiteral<"deepseek">, z.ZodLiteral<"custom">]>;
                baseUrl: z.ZodOptional<z.ZodString>;
                balanceUrl: z.ZodOptional<z.ZodString>;
                apiKey: z.ZodOptional<z.ZodString>;
                credential: z.ZodOptional<z.ZodString>;
            }, z.core.$strip>>;
        };
        readonly sourceLocation: {
            file: string;
            line: number;
            column: number;
        };
    }, {
        readonly id: "@deepseek-ai/dsh-client-ui-balance#balance/query";
        readonly service: "balance";
        readonly namespace: "balance";
        readonly method: "query";
        readonly invocation: {
            readonly kind: "direct";
        };
        readonly parameters: readonly [];
        readonly result: {
            readonly mode: "strict";
            readonly typeSymbol: "@deepseek-ai/dsh-client-ui-balance/types#BalanceResult[]";
            readonly schema: z.ZodArray<z.ZodObject<{
                id: z.ZodString;
                label: z.ZodString;
                ok: z.ZodBoolean;
                available: z.ZodOptional<z.ZodBoolean>;
                infos: z.ZodOptional<z.ZodArray<z.ZodObject<{
                    currency: z.ZodString;
                    total: z.ZodString;
                }, z.core.$strip>>>;
                error: z.ZodOptional<z.ZodString>;
                updatedAt: z.ZodNumber;
            }, z.core.$strip>>;
        };
        readonly sourceLocation: {
            file: string;
            line: number;
            column: number;
        };
    }, {
        readonly id: "@deepseek-ai/dsh-client-ui-balance#balance/add";
        readonly service: "balance";
        readonly namespace: "balance";
        readonly method: "add";
        readonly invocation: {
            readonly kind: "direct";
        };
        readonly parameters: readonly [{
            readonly name: "entry";
            readonly wire: "entry";
            readonly source: "json";
            readonly codec: {
                readonly mode: "strict";
                readonly typeSymbol: "@deepseek-ai/dsh-client-ui-balance/types#BalanceEntryInput";
                readonly schema: z.ZodObject<{
                    apiKey: z.ZodOptional<z.ZodString>;
                    label: z.ZodString;
                    baseUrl: z.ZodOptional<z.ZodString>;
                    kind: z.ZodUnion<readonly [z.ZodLiteral<"deepseek">, z.ZodLiteral<"custom">]>;
                    balanceUrl: z.ZodOptional<z.ZodString>;
                    credential: z.ZodOptional<z.ZodString>;
                }, z.core.$strip>;
            };
        }];
        readonly result: {
            readonly mode: "strict";
            readonly typeSymbol: "@deepseek-ai/dsh-client-ui-balance/types#BalanceEntry[]";
            readonly schema: z.ZodArray<z.ZodObject<{
                id: z.ZodString;
                label: z.ZodString;
                kind: z.ZodUnion<readonly [z.ZodLiteral<"deepseek">, z.ZodLiteral<"custom">]>;
                baseUrl: z.ZodOptional<z.ZodString>;
                balanceUrl: z.ZodOptional<z.ZodString>;
                apiKey: z.ZodOptional<z.ZodString>;
                credential: z.ZodOptional<z.ZodString>;
            }, z.core.$strip>>;
        };
        readonly sourceLocation: {
            file: string;
            line: number;
            column: number;
        };
    }, {
        readonly id: "@deepseek-ai/dsh-client-ui-balance#balance/remove";
        readonly service: "balance";
        readonly namespace: "balance";
        readonly method: "remove";
        readonly invocation: {
            readonly kind: "direct";
        };
        readonly parameters: readonly [{
            readonly name: "id";
            readonly wire: "id";
            readonly source: "json";
            readonly codec: {
                readonly mode: "strict";
                readonly typeSymbol: "string";
                readonly schema: z.ZodString;
            };
        }];
        readonly result: {
            readonly mode: "strict";
            readonly typeSymbol: "@deepseek-ai/dsh-client-ui-balance/types#BalanceEntry[]";
            readonly schema: z.ZodArray<z.ZodObject<{
                id: z.ZodString;
                label: z.ZodString;
                kind: z.ZodUnion<readonly [z.ZodLiteral<"deepseek">, z.ZodLiteral<"custom">]>;
                baseUrl: z.ZodOptional<z.ZodString>;
                balanceUrl: z.ZodOptional<z.ZodString>;
                apiKey: z.ZodOptional<z.ZodString>;
                credential: z.ZodOptional<z.ZodString>;
            }, z.core.$strip>>;
        };
        readonly sourceLocation: {
            file: string;
            line: number;
            column: number;
        };
    }, {
        readonly id: "@deepseek-ai/dsh-client-ui-balance#balance/update";
        readonly service: "balance";
        readonly namespace: "balance";
        readonly method: "update";
        readonly invocation: {
            readonly kind: "direct";
        };
        readonly parameters: readonly [{
            readonly name: "id";
            readonly wire: "id";
            readonly source: "json";
            readonly codec: {
                readonly mode: "strict";
                readonly typeSymbol: "string";
                readonly schema: z.ZodString;
            };
        }, {
            readonly name: "patch";
            readonly wire: "patch";
            readonly source: "json";
            readonly codec: {
                readonly mode: "strict";
                readonly typeSymbol: "@deepseek-ai/dsh-client-ui-balance/types#BalanceEntryInput (partial)";
                readonly schema: z.ZodObject<{
                    label: z.ZodOptional<z.ZodString>;
                    kind: z.ZodOptional<z.ZodUnion<readonly [z.ZodLiteral<"deepseek">, z.ZodLiteral<"custom">]>>;
                    baseUrl: z.ZodOptional<z.ZodString>;
                    balanceUrl: z.ZodOptional<z.ZodString>;
                    apiKey: z.ZodOptional<z.ZodString>;
                    credential: z.ZodOptional<z.ZodString>;
                }, z.core.$strip>;
            };
        }];
        readonly result: {
            readonly mode: "strict";
            readonly typeSymbol: "@deepseek-ai/dsh-client-ui-balance/types#BalanceEntry[]";
            readonly schema: z.ZodArray<z.ZodObject<{
                id: z.ZodString;
                label: z.ZodString;
                kind: z.ZodUnion<readonly [z.ZodLiteral<"deepseek">, z.ZodLiteral<"custom">]>;
                baseUrl: z.ZodOptional<z.ZodString>;
                balanceUrl: z.ZodOptional<z.ZodString>;
                apiKey: z.ZodOptional<z.ZodString>;
                credential: z.ZodOptional<z.ZodString>;
            }, z.core.$strip>>;
        };
        readonly sourceLocation: {
            file: string;
            line: number;
            column: number;
        };
    }];
};
export default TYPERT_REMOTE;
//# sourceMappingURL=remote.d.ts.map