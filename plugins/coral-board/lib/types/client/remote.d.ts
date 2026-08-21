/**
 * Browser half of the board Remote contract: strict wire descriptor for
 * `board/list` plus the typed client face. `ctx.remote.board.list()`
 * dispatches over the connection carrier to the node half's BoardService.
 * @module @deepseek-ai/dsh-client-coral-board/src/client/remote
 */
import { z } from 'zod';
import type { RemoteResult } from '@deepseek-ai/dsh-typert-protocol';
import type { BoardThread } from '../types.ts';
/** Strict wire schema for one board row. */
export declare const boardThreadSchema: z.ZodObject<{
    id: z.ZodString;
    title: z.ZodString;
    summary: z.ZodString;
    status: z.ZodString;
    steps: z.ZodNumber;
    doneSteps: z.ZodNumber;
    lastAdvanceBy: z.ZodString;
    updatedAt: z.ZodNumber;
    ageDays: z.ZodNumber;
    isCandidate: z.ZodBoolean;
}, z.core.$strip>;
/** Typed client face of the mounted `board` Remote namespace. */
export interface BoardRemoteApi {
    list(): Promise<RemoteResult<BoardThread[]>>;
}
/** Generated-shape Remote contribution consumed by `ctx.remote.$mount`. */
export declare const TYPERT_REMOTE: {
    readonly package: "@deepseek-ai/dsh-client-coral-board";
    readonly descriptors: readonly [{
        readonly id: "@deepseek-ai/dsh-client-coral-board#board/list";
        readonly service: "board";
        readonly namespace: "board";
        readonly method: "list";
        readonly invocation: {
            readonly kind: "direct";
        };
        readonly parameters: readonly [];
        readonly result: {
            readonly mode: "strict";
            readonly typeSymbol: "@deepseek-ai/dsh-client-coral-board/types#BoardThread[]";
            readonly schema: z.ZodArray<z.ZodObject<{
                id: z.ZodString;
                title: z.ZodString;
                summary: z.ZodString;
                status: z.ZodString;
                steps: z.ZodNumber;
                doneSteps: z.ZodNumber;
                lastAdvanceBy: z.ZodString;
                updatedAt: z.ZodNumber;
                ageDays: z.ZodNumber;
                isCandidate: z.ZodBoolean;
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