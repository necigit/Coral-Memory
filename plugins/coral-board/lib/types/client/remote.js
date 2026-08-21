/**
 * Browser half of the board Remote contract: strict wire descriptor for
 * `board/list` plus the typed client face. `ctx.remote.board.list()`
 * dispatches over the connection carrier to the node half's BoardService.
 * @module @deepseek-ai/dsh-client-coral-board/src/client/remote
 */
import { z } from 'zod';
/** Strict wire schema for one board row. */
export const boardThreadSchema = z.object({
    id: z.string(),
    title: z.string(),
    summary: z.string(),
    status: z.string(),
    steps: z.number(),
    doneSteps: z.number(),
    lastAdvanceBy: z.string(),
    updatedAt: z.number(),
    ageDays: z.number(),
    isCandidate: z.boolean(),
});
const source = { file: 'packages/client/coral-board/src/client/remote.ts', line: 1, column: 1 };
/** Generated-shape Remote contribution consumed by `ctx.remote.$mount`. */
export const TYPERT_REMOTE = {
    package: '@deepseek-ai/dsh-client-coral-board',
    descriptors: [
        {
            id: '@deepseek-ai/dsh-client-coral-board#board/list',
            service: 'board',
            namespace: 'board',
            method: 'list',
            invocation: { kind: 'direct' },
            parameters: [],
            result: {
                mode: 'strict',
                typeSymbol: '@deepseek-ai/dsh-client-coral-board/types#BoardThread[]',
                schema: z.array(boardThreadSchema),
            },
            sourceLocation: source,
        },
    ],
};
export default TYPERT_REMOTE;
//# sourceMappingURL=remote.js.map