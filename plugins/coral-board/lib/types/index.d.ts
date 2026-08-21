/**
 * Coral reasoning-thread task board, node half. Hosts the `board` Typert
 * Remote service: a read-only projection of coral_threads.json (the coral
 * memory system's reasoning-thread store). No tokens are spent — the file
 * is read on the node side and only small rows travel to the browser.
 * Management (archiving etc.) intentionally stays with the LLM via the
 * coral MCP tools; the capsule is a watch-only board.
 *
 * Threads file location: env CORAL_THREADS_PATH > $DSH_HOME/coral_board.json
 * {threadsPath} > the author's default below.
 * @module @deepseek-ai/dsh-client-coral-board
 */
import type { Context } from '@deepseek-ai/cordis';
import { TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol';
import type { BoardThread } from './types.ts';
/** The `board` Remote service: read-only thread board projection. */
export declare class BoardService extends TypertRemoteService {
    constructor(ctx: Context);
    /** List all threads with projected board rows (active first, newest first). */
    list(): Promise<BoardThread[]>;
}
/** Host plugin body: mount the service. */
export declare function apply(ctx: Context): void;
export default BoardService;
//# sourceMappingURL=index.d.ts.map