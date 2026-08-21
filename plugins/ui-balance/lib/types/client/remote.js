/**
 * Browser half of the balance Remote contract: the strict wire descriptors
 * (hand-written twin of what the typert generator would emit) plus the typed
 * client face. The client bundle `$mount`s this contribution onto
 * `ctx.remote`, so `ctx.remote.balance.list()` etc. dispatch over the
 * connection carrier to the node half's BalanceService.
 * @module @deepseek-ai/dsh-client-ui-balance/src/client/remote
 */
import { z } from 'zod';
/** Strict wire schema for one stored entry (inline keys never arrive). */
export const balanceEntrySchema = z.object({
    id: z.string(),
    label: z.string(),
    kind: z.union([z.literal('deepseek'), z.literal('custom')]),
    baseUrl: z.string().optional(),
    balanceUrl: z.string().optional(),
    apiKey: z.string().optional(),
    credential: z.string().optional(),
});
/** Strict wire schema for add() input. */
export const balanceEntryInputSchema = balanceEntrySchema.omit({ id: true });
/** Strict wire schema for update() patches. */
export const balanceEntryPatchSchema = z.object({
    label: z.string().optional(),
    kind: z.union([z.literal('deepseek'), z.literal('custom')]).optional(),
    baseUrl: z.string().optional(),
    balanceUrl: z.string().optional(),
    apiKey: z.string().optional(),
    credential: z.string().optional(),
});
/** Strict wire schema for one currency bucket. */
export const balanceInfoSchema = z.object({
    currency: z.string(),
    total: z.string(),
});
/** Strict wire schema for one query outcome. */
export const balanceResultSchema = z.object({
    id: z.string(),
    label: z.string(),
    ok: z.boolean(),
    available: z.boolean().optional(),
    infos: z.array(balanceInfoSchema).optional(),
    error: z.string().optional(),
    updatedAt: z.number(),
});
const source = { file: 'packages/client/ui-balance/src/client/remote.ts', line: 1, column: 1 };
/** Generated-shape Remote contribution consumed by `ctx.remote.$mount`. */
export const TYPERT_REMOTE = {
    package: '@deepseek-ai/dsh-client-ui-balance',
    descriptors: [
        {
            id: '@deepseek-ai/dsh-client-ui-balance#balance/list',
            service: 'balance',
            namespace: 'balance',
            method: 'list',
            invocation: { kind: 'direct' },
            parameters: [],
            result: {
                mode: 'strict',
                typeSymbol: '@deepseek-ai/dsh-client-ui-balance/types#BalanceEntry[]',
                schema: z.array(balanceEntrySchema),
            },
            sourceLocation: source,
        },
        {
            id: '@deepseek-ai/dsh-client-ui-balance#balance/query',
            service: 'balance',
            namespace: 'balance',
            method: 'query',
            invocation: { kind: 'direct' },
            parameters: [],
            result: {
                mode: 'strict',
                typeSymbol: '@deepseek-ai/dsh-client-ui-balance/types#BalanceResult[]',
                schema: z.array(balanceResultSchema),
            },
            sourceLocation: source,
        },
        {
            id: '@deepseek-ai/dsh-client-ui-balance#balance/add',
            service: 'balance',
            namespace: 'balance',
            method: 'add',
            invocation: { kind: 'direct' },
            parameters: [
                {
                    name: 'entry',
                    wire: 'entry',
                    source: 'json',
                    codec: {
                        mode: 'strict',
                        typeSymbol: '@deepseek-ai/dsh-client-ui-balance/types#BalanceEntryInput',
                        schema: balanceEntryInputSchema,
                    },
                },
            ],
            result: {
                mode: 'strict',
                typeSymbol: '@deepseek-ai/dsh-client-ui-balance/types#BalanceEntry[]',
                schema: z.array(balanceEntrySchema),
            },
            sourceLocation: source,
        },
        {
            id: '@deepseek-ai/dsh-client-ui-balance#balance/remove',
            service: 'balance',
            namespace: 'balance',
            method: 'remove',
            invocation: { kind: 'direct' },
            parameters: [
                {
                    name: 'id',
                    wire: 'id',
                    source: 'json',
                    codec: { mode: 'strict', typeSymbol: 'string', schema: z.string() },
                },
            ],
            result: {
                mode: 'strict',
                typeSymbol: '@deepseek-ai/dsh-client-ui-balance/types#BalanceEntry[]',
                schema: z.array(balanceEntrySchema),
            },
            sourceLocation: source,
        },
        {
            id: '@deepseek-ai/dsh-client-ui-balance#balance/update',
            service: 'balance',
            namespace: 'balance',
            method: 'update',
            invocation: { kind: 'direct' },
            parameters: [
                {
                    name: 'id',
                    wire: 'id',
                    source: 'json',
                    codec: { mode: 'strict', typeSymbol: 'string', schema: z.string() },
                },
                {
                    name: 'patch',
                    wire: 'patch',
                    source: 'json',
                    codec: {
                        mode: 'strict',
                        typeSymbol: '@deepseek-ai/dsh-client-ui-balance/types#BalanceEntryInput (partial)',
                        schema: balanceEntryPatchSchema,
                    },
                },
            ],
            result: {
                mode: 'strict',
                typeSymbol: '@deepseek-ai/dsh-client-ui-balance/types#BalanceEntry[]',
                schema: z.array(balanceEntrySchema),
            },
            sourceLocation: source,
        },
    ],
};
export default TYPERT_REMOTE;
//# sourceMappingURL=remote.js.map